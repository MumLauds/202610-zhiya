"""Local ASR and reading-report provider; run behind the rehearsal HTTPS proxy."""

import base64
import binascii
import io
import json
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/asr", "/report"):
            self.send_error(404)
            return
        try:
            size = int(self.headers["Content-Length"])
            if not 0 < size <= 6 * 1024 * 1024:
                raise ValueError
            request = json.loads(self.rfile.read(size))
            messages = request["messages"]
            if self.path == "/asr":
                item = messages[0]["content"][0]
                scheme, encoded = item["input_audio"]["data"].split(",", 1)
                if item["type"] != "input_audio" or scheme != "data:audio/wav;base64":
                    raise ValueError
                with wave.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as wav:
                    frames = wav.getnframes()
                    if ((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000)
                            or not frames or len(wav.readframes(frames)) != 2 * frames):
                        raise ValueError
                content = "小船停在哪里"
                self.measurements = {"frames": frames, "hz": 16000, "channels": 1, "bits": 16}
            else:
                prompt = "\n".join(m["content"] for m in messages if m["role"] == "user")
                if "本次共读阅读了第 1 页。" not in prompt or "小园把一只纸船放进水里。" not in prompt:
                    raise ValueError
                content = '{"suggestionId":"NONE"}'
                self.measurements = {"visitedPages": [1]}
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, binascii.Error, wave.Error, EOFError):
            self.send_error(422, "Unexpected rehearsal payload")
            return
        body = json.dumps({"choices": [{"finish_reason": "stop",
                                        "message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code="-", size="-"):
        route = {"/asr": "asr", "/report": "report"}.get(getattr(self, "path", ""), "unknown")
        entry = {"call": route, "status": int(code)}
        if code == 200:
            entry.update(self.measurements)
        print(json.dumps(entry), flush=True)

    def log_message(self, *_):
        # Keep request headers, recordings and texts out of the logs.
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9080), Provider).serve_forever()
