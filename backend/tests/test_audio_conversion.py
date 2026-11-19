import asyncio
import io
import shutil
import subprocess
import sys
import wave
from unittest.mock import patch

import pytest

from app.audio_conversion import MAX_PCM_BYTES, MAX_UPLOAD_BYTES, normalize_recording, pcm_wav
from app.errors import ServiceError


def test_valid_wav_needs_no_converter_and_rejects_bad_inputs():
    source=pcm_wav(b"\x00\x00"*160)
    with patch("asyncio.create_subprocess_exec") as spawn:
        assert asyncio.run(normalize_recording(source,"audio/wav"))==source
        for audio,mime,code in [(b"","audio/wav","AUDIO_INVALID"),(b"x","text/plain","AUDIO_UNSUPPORTED_FORMAT"),
            (b"x"*(MAX_UPLOAD_BYTES+1),"audio/webm","AUDIO_TOO_LARGE"),
            (source[:-1],"audio/wav","AUDIO_INVALID"),(pcm_wav(b"x"*(MAX_PCM_BYTES+2)),"audio/wav","AUDIO_TOO_LONG")]:
            with pytest.raises(ServiceError) as failure:
                asyncio.run(normalize_recording(audio,mime))
            assert failure.value.code==code
        spawn.assert_not_called()


def test_converter_missing_is_safe_and_does_not_echo_input():
    with patch("asyncio.create_subprocess_exec",side_effect=FileNotFoundError("private-path")):
        with pytest.raises(ServiceError) as failure:
            asyncio.run(normalize_recording(b"private-audio","audio/webm"))
    assert failure.value.code=="AUDIO_CONVERTER_UNAVAILABLE"
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("mode",["timeout","cancel","oversize","malformed"])
def test_process_terminated_on_timeout_cancel_or_output_limit(monkeypatch,mode):
    original=asyncio.create_subprocess_exec
    processes=[]
    timeout_scope=None
    async def spawn(*args,**kwargs):
        assert args[0]=="ffmpeg" and args[args.index('-protocol_whitelist')+1]=='pipe'
        assert kwargs['stderr']==asyncio.subprocess.DEVNULL
        script="import time;time.sleep(30)"
        if mode=='oversize': script=f"import sys;sys.stdout.buffer.write(b'x'*{MAX_PCM_BYTES+2});sys.stdout.buffer.flush()"
        if mode=='malformed': script="import sys;sys.stdout.buffer.write(b'x');sys.exit(1)"
        process=await original(sys.executable,'-c',script,**kwargs)
        processes.append(process)
        if mode=='timeout':
            # Expire on the next loop turn, after the decoder receives the handle.
            timeout_scope.reschedule(asyncio.get_running_loop().time())
        return process
    monkeypatch.setattr("asyncio.create_subprocess_exec",spawn)
    if mode=='timeout':
        original_timeout=asyncio.timeout
        def timeout_after_spawn(_delay):
            nonlocal timeout_scope
            # Keep the real timeout/cancellation path without timing process startup.
            timeout_scope=original_timeout(None)
            return timeout_scope
        monkeypatch.setattr('app.audio_conversion.asyncio.timeout',timeout_after_spawn)
    async def scenario():
        task=asyncio.create_task(normalize_recording(b"sample","audio/webm;codecs=opus"))
        if mode=='cancel':
            while not processes: await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        else:
            with pytest.raises(ServiceError) as failure: await task
            assert failure.value.code=={'timeout':'AUDIO_CONVERSION_TIMEOUT','oversize':'AUDIO_TOO_LONG','malformed':'AUDIO_INVALID'}[mode]
            if mode=='timeout': assert failure.value.status_code==504
        assert processes and all(p.returncode is not None for p in processes)
    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which('ffmpeg') is None,reason='FFmpeg integration runs in the Docker image')
@pytest.mark.parametrize('container,codec,mime',[('webm','libopus','audio/webm;codecs=opus'),('ogg','libopus','audio/ogg'),('mp3','libmp3lame','audio/mpeg'),('mp4','aac','audio/mp4'),('wav','pcm_f32le','audio/wav')])
def test_real_ffmpeg_browser_recording(container,codec,mime,tmp_path):
    # WAV needs a seekable output so FFmpeg finalizes RIFF/data lengths.
    target = str(tmp_path / 'float.wav') if container == 'wav' else 'pipe:1'
    generated=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
        '-t','0.2','-ac','2','-c:a',codec,*(['-movflags','frag_keyframe+empty_moov'] if container=='mp4' else []),
        '-f',container,target],capture_output=True,check=True)
    sample=(tmp_path / 'float.wav').read_bytes() if container == 'wav' else generated.stdout
    result=asyncio.run(normalize_recording(sample,mime))
    with wave.open(io.BytesIO(result),'rb') as source:
        assert (source.getnchannels(),source.getsampwidth(),source.getframerate())==(1,2,16000)
        assert 0.15 < source.getnframes()/16000 < 0.3
