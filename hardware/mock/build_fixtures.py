#!/usr/bin/env python3
"""Build deterministic DEMO fixtures for the hardware mock layer (H05).

The builder reads, but never writes, the repository seed files and the H03
asset manifest. It selects only the 20 original storybooks in
``data/seed/cover_mapping.json``; the legacy "逃家小兔" linkage book is not
part of the device DEMO set.

Usage:
    python hardware/mock/build_fixtures.py
    python hardware/mock/build_fixtures.py --check
    python hardware/mock/build_fixtures.py --output-dir hardware/mock/fixtures
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "hardware" / "mock" / "fixtures"
SEED_FILES = (
    "storybooks.json",
    "story_versions.json",
    "story_pages.json",
    "cover_mapping.json",
)
OUTPUT_FILES = (
    "storybooks.json",
    "story-details.json",
    "demo-session.json",
    "voice-scenarios.json",
    "error-scenarios.json",
)
EXPECTED_BOOKS = 20
EXPECTED_PAGES = 104
EXPECTED_ASSETS = 40
SOURCE_DIGEST = "sha256-utf8-lf-v1"
NOTICE = (
    "本地 DEMO 数据：仅供知芽课程项目硬件端和 Mock 自动化测试使用，"
    "不代表绘本已获得正式在线发布或传播授权；源种子中 20 本原创绘本为 PUBLISHED，"
    "1 本《逃家小兔》联调草稿为 DRAFT 且不纳入本 DEMO。"
    "coverRef/imageRef 必须保持 null，不得当作 API-024 的受控媒体 resource key；"
    "H03 内页资源尚未由独立任务交付，localPageAsset 保持 null，禁止用封面冒充正文插画。"
)


class FixtureError(RuntimeError):
    """A source file or fixture precondition is invalid."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FixtureError(f"缺少源文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureError(f"JSON 无法解析: {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text_file(path: Path) -> str:
    """Hash UTF-8 text after normalizing CRLF/CR to LF.

    Git stores the seed JSON with mixed EOL styles (some blobs LF, some CRLF),
    while a Windows checkout may materialize all of them as CRLF. Hashing raw
    bytes therefore changes between clean Git blobs and the working tree even
    when the content is identical. The source digest is explicitly defined as
    UTF-8 text with LF line endings; no whitespace or JSON content is dropped.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FixtureError(f"源文件不是无 BOM 的 UTF-8 文本: {path}: {exc}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def relative_repo_path(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def display_path(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureError(message)


def require_int(value: Any, name: str, minimum: int = 0) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{name} 必须是整数")
    require(value >= minimum, f"{name} 必须 >= {minimum}")
    return value


def resolve_version(versions: list[dict[str, Any]], storybook_id: int) -> dict[str, Any]:
    """Resolve exactly one version by storybookId, never by array position."""
    matches = [item for item in versions if item.get("storybook_id") == storybook_id]
    require(len(matches) == 1, f"storybookId={storybook_id} 必须恰好对应一个版本，实际 {len(matches)} 个")
    return matches[0]


def age_band(age_min: int, age_max: int) -> str | None:
    bands = {(3, 4): "AGE_3_4", (4, 5): "AGE_4_5", (5, 6): "AGE_5_6"}
    return bands.get((age_min, age_max))


def validate_pages(version_id: int, pages: list[dict[str, Any]]) -> None:
    require(bool(pages), f"storyVersionId={version_id} 没有页面")
    ordered = sorted(pages, key=lambda item: item["page_no"])
    numbers = [require_int(item.get("page_no"), "page_no", 1) for item in ordered]
    require(numbers == list(range(1, len(ordered) + 1)),
            f"storyVersionId={version_id} 页码必须从 1 连续，实际 {numbers}")
    for index, item in enumerate(ordered):
        next_no = item.get("default_next_page_number")
        if index == len(ordered) - 1:
            require(next_no is None,
                    f"storyVersionId={version_id} 末页 default_next_page_number 必须为 null")
        else:
            require(next_no == numbers[index + 1],
                    f"storyVersionId={version_id} 第 {item['page_no']} 页后继应为 {numbers[index + 1]}，实际 {next_no}")
        require(isinstance(item.get("body_text"), str) and item["body_text"].strip() != "",
                f"storyVersionId={version_id} 第 {item['page_no']} 页正文为空")


def build_local_cover(
    repo_root: Path,
    mapping: dict[str, Any],
    assets_by_role: dict[str, list[dict[str, Any]]],
    storybook_id: int,
    story_version_id: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for role in ("THUMBNAIL", "DETAIL"):
        matches = assets_by_role.get(role, [])
        require(len(matches) == 1, f"storybookId={storybook_id} 的 {role} 资源必须恰好一个")
        asset = matches[0]
        require(asset.get("bookNo") == mapping["book_no"], f"storybookId={storybook_id} 的 bookNo 不一致")
        require(asset.get("storybookId") == storybook_id, f"storybookId={storybook_id} 的列表 ID 不一致")
        require(asset.get("storyVersionId") == story_version_id,
                f"storybookId={storybook_id} 的 storyVersionId 不一致")
        require(asset.get("sourcePath") == mapping["cover_path"], f"storybookId={storybook_id} 的 sourcePath 不一致")
        asset_path = asset.get("assetPath")
        require(isinstance(asset_path, str) and asset_path.startswith("hardware/assets/generated/"),
                f"storybookId={storybook_id} 的 assetPath 不在预期目录")
        path = repo_root / asset_path
        require(path.is_file(), f"缺少 H03 资源: {asset_path}")
        require(sha256_file(path) == asset.get("sha256"), f"H03 资源 SHA-256 不一致: {asset_path}")
        require(path.stat().st_size == asset.get("fileSize"), f"H03 资源大小不一致: {asset_path}")
        require(asset.get("format") == "JPEG", f"H03 资源必须是 JPEG: {asset_path}")
        result[role.lower()] = {
            "assetRole": role,
            "assetPath": asset_path,
            "devicePath": asset.get("devicePath"),
            "width": asset["width"],
            "height": asset["height"],
            "format": asset["format"],
            "fileSize": asset["fileSize"],
            "sha256": asset["sha256"],
        }
    return result


def build_payloads(repo_root: Path = REPO_ROOT) -> dict[str, dict[str, Any]]:
    seed_dir = repo_root / "data" / "seed"
    storybooks = read_json(seed_dir / "storybooks.json")
    versions = read_json(seed_dir / "story_versions.json")
    pages = read_json(seed_dir / "story_pages.json")
    mapping = read_json(seed_dir / "cover_mapping.json")
    asset_manifest_path = repo_root / "hardware" / "assets" / "generated" / "assets-manifest.json"
    asset_manifest = read_json(asset_manifest_path)

    require(isinstance(storybooks, list), "storybooks.json 必须是数组")
    require(isinstance(versions, list), "story_versions.json 必须是数组")
    require(isinstance(pages, list), "story_pages.json 必须是数组")
    require(isinstance(mapping, list), "cover_mapping.json 必须是数组")
    require(len(mapping) == EXPECTED_BOOKS, f"cover_mapping 必须为 {EXPECTED_BOOKS} 条")
    require(len(asset_manifest.get("assets", [])) == EXPECTED_ASSETS, f"H03 资源必须为 {EXPECTED_ASSETS} 个")

    storybook_ids = {item.get("id") for item in storybooks}
    require(len(storybook_ids) == len(storybooks), "storybooks.json 存在重复 id")

    assets_by_storybook: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for asset in asset_manifest["assets"]:
        identify = asset.get("storybookId")
        role = asset.get("assetRole")
        require(isinstance(identify, int), "H03 资源 storybookId 必须是整数")
        require(role in ("THUMBNAIL", "DETAIL"), f"H03 资源角色非法: {role}")
        assets_by_storybook.setdefault(identify, {}).setdefault(role, []).append(asset)

    records: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    selected_pages = 0

    for cover in mapping:
        book_no = cover.get("book_no")
        storybook_id = require_int(cover.get("storybook_id"), "cover_mapping.storybook_id", 1)
        cover_path = cover.get("cover_path")
        require(isinstance(book_no, str) and book_no, "cover_mapping.book_no 缺失")
        require(isinstance(cover_path, str) and cover_path, "cover_mapping.cover_path 缺失")
        require(not Path(cover_path).is_absolute() and ".." not in Path(cover_path).parts,
                f"封面路径必须位于仓库相对目录内: {cover_path}")
        require(cover_path.startswith("knowledge-base_v2.5/图片/"),
                f"封面路径不在预期素材目录: {cover_path}")
        require(storybook_id in storybook_ids, f"cover_mapping 引用了不存在的 storybookId={storybook_id}")
        version = resolve_version(versions, storybook_id)
        version_id = require_int(version.get("id"), "story_versions.id", 1)
        require(isinstance(version.get("title"), str) and version["title"].strip() != "",
                f"storybookId={storybook_id} 标题缺失")
        require(isinstance(version.get("status"), str) and version["status"],
                f"storybookId={storybook_id} 状态缺失")
        version_pages = [item for item in pages if item.get("version_id") == version_id]
        validate_pages(version_id, version_pages)
        local_cover = build_local_cover(
            repo_root, cover, assets_by_storybook.get(storybook_id, {}), storybook_id, version_id
        )
        selected_pages += len(version_pages)
        age_min = require_int(version.get("age_min"), "age_min", 0)
        age_max = require_int(version.get("age_max"), "age_max", 0)
        band = age_band(age_min, age_max)
        require(band is not None, f"storybookId={storybook_id} 的年龄段不受支持")
        record = {
            "storybookId": storybook_id,
            "storyVersionId": version_id,
            "versionNumber": require_int(version.get("version_no"), "version_no", 1),
            "title": version.get("title"),
            "status": version.get("status"),
            "ageMin": age_min,
            "ageMax": age_max,
            "ageBand": band,
            "topics": list(version.get("topics_json") or []),
            "pageCount": len(version_pages),
            "totalPages": max(item["page_no"] for item in version_pages),
            "coverRef": None,
            "localCover": local_cover,
            "demoOnly": True,
        }
        detail = dict(record)
        detail.update({
            "pages": [
                {
                    "pageNumber": item["page_no"],
                    "text": item["body_text"],
                    "imageRef": None,
                    "defaultNextPageNumber": item.get("default_next_page_number"),
                    "imageStatus": "MISSING_INNER_ILLUSTRATION",
                    # Reserved hook for the independent H03 page-asset task.
                    # Do not populate a path/hash until that task ships audited files.
                    "localPageAsset": None,
                }
                for item in sorted(version_pages, key=lambda item: item["page_no"])
            ],
            "branches": [],
            "summary": None,
        })
        records.append(record)
        details.append(detail)

    require(len(records) == EXPECTED_BOOKS, f"必须生成 {EXPECTED_BOOKS} 本原创绘本")
    require(selected_pages == EXPECTED_PAGES, f"原创正文必须为 {EXPECTED_PAGES} 页，实际 {selected_pages}")
    require(1 not in {record["storybookId"] for record in records}, "不得把旧版联调绘本纳入 DEMO")
    require(all(record["status"] == "PUBLISHED" for record in records),
            "H05 DEMO 必须只选择种子中 PUBLISHED 的 20 本原创绘本")
    require(
        all("pages" not in record and "branches" not in record for record in records),
        "storybooks.json 必须保持轻量元数据，不得包含 pages/branches",
    )

    source_files = []
    for name in SEED_FILES:
        path = seed_dir / name
        source_files.append({
            "path": relative_repo_path(repo_root, path),
            "sha256": sha256_text_file(path),
        })
    source_files.append({
        "path": relative_repo_path(repo_root, asset_manifest_path),
        "sha256": sha256_text_file(asset_manifest_path),
    })

    storybook_fixture = {
        "schemaVersion": "1.0",
        "kind": "zhiya-hardware-demo-storybooks",
        "notice": NOTICE,
        "demoOnly": True,
        "sourceDigest": SOURCE_DIGEST,
        "sourceFiles": source_files,
        "items": records,
    }
    details_fixture = {
        "schemaVersion": "1.0",
        "kind": "zhiya-hardware-demo-story-details",
        "notice": NOTICE,
        "demoOnly": True,
        "sourceDigest": SOURCE_DIGEST,
        "sourceFiles": source_files,
        "items": details,
    }
    demo_session = {
        "schemaVersion": "1.0",
        "kind": "zhiya-hardware-demo-session",
        "notice": NOTICE,
        "demoOnly": True,
        "profile": {
            "childProfileId": 1,
            "nickname": "本地演示",
            "ageBand": "AGE_3_4",
            "preferredMinutes": 10,
            "demoOnly": True,
        },
        "session": {
            "readingSessionId": 1,
            "storyVersionId": records[0]["storyVersionId"],
            "currentPage": 1,
            "visitedPageNumbers": [],
            "mode": "PARENT_CHILD",
            "status": "CREATED",
            "revision": 0,
            "expiresAt": None,
            "demoOnly": True,
        },
    }
    return {
        "storybooks.json": storybook_fixture,
        "story-details.json": details_fixture,
        "demo-session.json": demo_session,
        "voice-scenarios.json": build_voice_scenarios(),
        "error-scenarios.json": build_error_scenarios(),
    }


def state_timeline(*entries: tuple[str, str, str, int]) -> list[dict[str, Any]]:
    return [
        {"state": state, "label": label, "hint": hint, "delayMs": delay_ms, "isError": state.startswith("ERROR_")}
        for state, label, hint, delay_ms in entries
    ]


def build_voice_scenarios() -> dict[str, Any]:
    notice = (
        "语音场景只模拟 H04 状态机的界面文案与迁移，不包含真实录音、真实 ASR/TTS、"
        "真实模型回答或可播放音频。audioUrl 必须为 null，不得伪装成 API-023 的真实输出。"
    )
    scenarios = [
        {
            "scenarioId": "success",
            "title": "模拟识别与回答成功",
            "description": "只演示家长确认后的目标链路；真实 API-022 的 reliable 固定为 false，儿童模式不能据此自动问答。",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下，最多 15 秒", 800),
                ("UPLOADING", "正在上传…", "本地 Mock，不上传真实录音", 600),
                ("RECOGNIZING", "正在听懂你说的话…", "识别结果为固定测试文本", 600),
                ("THINKING", "让我想一想…", "本地 Mock 不调用云端模型", 900),
                ("SYNTHESIZING", "正在把答案读出来…", "只返回测试播放状态", 700),
                ("PLAYING", "知芽在回答", "模拟播放，不产生真实音频", 1200),
                ("IDLE", "还想问点什么？", "关闭返回阅读页", 600),
            ),
            "finalState": "IDLE",
            "outcome": "SUCCESS",
            "asrText": "小狐狸为什么带着雨伞？",
            "reliable": True,
            "reliableIsSimulated": True,
            "consumer": "PARENT_CONFIRMED",
            "answerText": "因为下雨了，小狐狸撑起雨伞，想快点回家。",
            "answerSource": "MOCK_TEMPLATE",
            "tts": {
                "status": "SIMULATED",
                "audioUrl": None,
                "playbackMode": "SIMULATED",
                "durationMs": 2400,
            },
            "recoverTo": None,
            "demoOnly": True,
        },
        {
            "scenarioId": "reliable_false_child",
            "title": "识别结果不可信（儿童重录）",
            "description": "模拟真实 API-022 的 reliable=false 限制；儿童模式只能重录，不能自动进入问答。",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下", 700),
                ("UPLOADING", "正在上传…", "本地 Mock", 500),
                ("RECOGNIZING", "正在听懂你说的话…", "识别结果不可信", 500),
                ("ERROR_ASR", "没听清这句话，换个说法试试", "请重录或取消", 700),
            ),
            "finalState": "ERROR_ASR",
            "outcome": "RELIABLE_FALSE",
            "asrText": "小狐狸为什么……",
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "CHILD",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
        {
            "scenarioId": "asr_failed",
            "title": "识别失败",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下", 700),
                ("UPLOADING", "正在上传…", "本地 Mock", 500),
                ("RECOGNIZING", "正在听懂你说的话…", "识别失败", 500),
                ("ERROR_ASR", "没听清这句话，换个说法试试", "重录或取消", 700),
            ),
            "finalState": "ERROR_ASR",
            "outcome": "ASR_FAILED",
            "asrText": None,
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_OR_CHILD",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
        {
            "scenarioId": "ai_timeout",
            "title": "AI 问答超时",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下", 700),
                ("UPLOADING", "正在上传…", "本地 Mock", 500),
                ("RECOGNIZING", "正在听懂你说的话…", "识别成功", 500),
                ("THINKING", "让我想一想…", "模拟等待超过 20 秒", 1200),
                ("ERROR_TIMEOUT", "想得太久啦，再问一次好吗", "重试或取消", 700),
            ),
            "finalState": "ERROR_TIMEOUT",
            "outcome": "AI_TIMEOUT",
            "asrText": "小狐狸要去哪里？",
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_CONFIRMED",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
        {
            "scenarioId": "tts_failed",
            "title": "语音合成失败",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下", 700),
                ("UPLOADING", "正在上传…", "本地 Mock", 500),
                ("RECOGNIZING", "正在听懂你说的话…", "识别成功", 500),
                ("THINKING", "让我想一想…", "模拟已拿到文字答案", 900),
                ("SYNTHESIZING", "正在把答案读出来…", "模拟 TTS 失败", 700),
                ("ERROR_TTS", "答案有了，但声音没准备好", "只看文字或重试", 700),
            ),
            "finalState": "ERROR_TTS",
            "outcome": "TTS_FAILED",
            "asrText": "小狐狸为什么带着雨伞？",
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_CONFIRMED",
            "answerText": "因为下雨了，小狐狸撑起雨伞，想快点回家。",
            "answerSource": "MOCK_TEMPLATE",
            "tts": None,
            "recoverTo": "SYNTHESIZING",
            "recoveryActions": [
                {
                    "actionId": "RETRY_SYNTHESIS",
                    "label": "重试",
                    "targetState": "SYNTHESIZING",
                    "requiresSavedInteractionEventId": True,
                    "rerunQuestion": False,
                    "rerunRecording": False,
                    "recordsNewEvent": False,
                },
                {
                    "actionId": "TEXT_ONLY",
                    "label": "只看文字",
                    "targetState": "IDLE",
                    "requiresSavedInteractionEventId": True,
                    "rerunQuestion": False,
                    "rerunRecording": False,
                    "recordsNewEvent": False,
                },
            ],
            "demoOnly": True,
        },
        {
            "scenarioId": "cancelled",
            "title": "用户取消录音",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "用户点击取消", 500),
                ("IDLE", "先不问啦", "返回阅读页", 300),
            ),
            "finalState": "IDLE",
            "outcome": "CANCELLED",
            "asrText": None,
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_OR_CHILD",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
        {
            "scenarioId": "network_lost",
            "title": "上传时网络中断",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "说完再点一下", 600),
                ("UPLOADING", "正在上传…", "模拟网络断开", 500),
                ("ERROR_NETWORK", "网络断开啦，先读故事吧", "网络恢复或取消", 600),
                ("IDLE", "连接已恢复", "可重新提问", 500),
            ),
            "finalState": "IDLE",
            "outcome": "NETWORK_LOST",
            "asrText": None,
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_OR_CHILD",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
        {
            "scenarioId": "too_short",
            "title": "录音为空或过短",
            "states": state_timeline(
                ("IDLE", "想问什么就问吧", "点麦克风开始", 0),
                ("RECORDING", "我在听…", "0.3 秒后停止", 300),
                ("ERROR_TOO_SHORT", "没有听清，再说一次吧", "重试或取消", 500),
            ),
            "finalState": "ERROR_TOO_SHORT",
            "outcome": "TOO_SHORT",
            "asrText": None,
            "reliable": False,
            "reliableIsSimulated": True,
            "consumer": "PARENT_OR_CHILD",
            "answerText": None,
            "answerSource": None,
            "tts": None,
            "recoverTo": "IDLE",
            "demoOnly": True,
        },
    ]
    return {
        "schemaVersion": "1.0",
        "kind": "zhiya-hardware-demo-voice-scenarios",
        "notice": notice,
        "demoOnly": True,
        "states": ["IDLE", "RECORDING", "UPLOADING", "RECOGNIZING", "THINKING", "SYNTHESIZING", "PLAYING"],
        "errorStates": ["ERROR_ASR", "ERROR_TIMEOUT", "ERROR_TTS", "ERROR_NETWORK", "ERROR_TOO_SHORT"],
        "scenarios": scenarios,
    }


def build_error_scenarios() -> dict[str, Any]:
    scenarios = [
        {
            "scenarioId": "AUTH_REQUIRED", "status": 401, "code": "AUTH_REQUIRED",
            "message": "本地 Mock 没有设备凭证", "retryable": False,
            "appliesTo": ["/api/v1/*"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "FORBIDDEN", "status": 403, "code": "FORBIDDEN",
            "message": "本地 Mock 的授权范围拒绝", "retryable": False,
            "appliesTo": ["/api/v1/*"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "RESOURCE_NOT_FOUND", "status": 404, "code": "RESOURCE_NOT_FOUND",
            "message": "本地 Mock 资源不存在", "retryable": False,
            "appliesTo": ["/api/v1/*"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "SESSION_CONFLICT", "status": 409, "code": "SESSION_CONFLICT",
            "message": "Mock 会话修订冲突，请先读取会话", "retryable": False,
            "appliesTo": ["/api/v1/reading-sessions/*/events", "/api/v1/reading-sessions/*/questions",
                          "/api/v1/reading-sessions/*/completions"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "IDEMPOTENCY_CONFLICT", "status": 409, "code": "IDEMPOTENCY_CONFLICT",
            "message": "Mock 请求标识已用于其他操作", "retryable": False,
            "appliesTo": ["/api/v1/reading-sessions/*/events", "/api/v1/reading-sessions/*/questions",
                          "/api/v1/reading-sessions/*/completions"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "SESSION_ENDED", "status": 409, "code": "SESSION_CONFLICT",
            "message": "本次阅读已结束", "retryable": False,
            "appliesTo": ["/api/v1/reading-sessions/*/events", "/api/v1/reading-sessions/*/questions"],
            "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "INVALID_PAGE", "status": 422, "code": "VALIDATION_ERROR",
            "message": "Mock 页码不合法", "retryable": False,
            "appliesTo": ["/api/v1/reading-sessions/*/events"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "VALIDATION_ERROR", "status": 422, "code": "VALIDATION_ERROR",
            "message": "Mock 请求字段不合法", "retryable": False,
            "appliesTo": ["/api/v1/*"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "DEPENDENCY_UNAVAILABLE", "status": 503, "code": "DEPENDENCY_UNAVAILABLE",
            "message": "Mock 依赖暂不可用", "retryable": True,
            "retryableByMethod": {"GET": True, "POST": False},
            "appliesTo": ["/api/v1/*"], "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "REQUEST_OUTCOME_UNKNOWN", "status": 504, "code": "REQUEST_OUTCOME_UNKNOWN",
            "message": "Mock 写入结果未知，请先读取会话", "retryable": False,
            "appliesTo": ["/api/v1/reading-sessions/*/events", "/api/v1/reading-sessions/*/questions",
                          "/api/v1/reading-sessions/*/completions"],
            "mode": "error", "demoOnly": True,
        },
        {
            "scenarioId": "NO_BOOKS", "status": 200, "code": None,
            "message": "Mock 空书架", "retryable": False,
            "appliesTo": ["/api/v1/storybooks"], "mode": "payload",
            "payload": {"items": [], "page": 1, "pageSize": 20}, "demoOnly": True,
        },
        {
            "scenarioId": "COVER_MISSING", "status": 200, "code": None,
            "message": "Mock 移除本地封面，模拟缺图", "retryable": False,
            "appliesTo": ["/api/v1/storybooks", "/api/v1/story-versions/*"], "mode": "payload",
            "action": "stripLocalCover", "demoOnly": True,
        },
        {
            "scenarioId": "VOICE_BUSY", "status": 429, "code": "VOICE_BUSY",
            "message": "Mock 语音并发忙碌", "retryable": True, "retryAfter": 1,
            "appliesTo": ["/mock/voice/*"], "mode": "error", "demoOnly": True,
        },
    ]
    return {
        "schemaVersion": "1.0",
        "kind": "zhiya-hardware-demo-error-scenarios",
        "notice": "仅用于本地 Mock 错误注入；响应不是真实后端记录，也不代表真实授权结果。",
        "demoOnly": True,
        "scenarios": scenarios,
    }


def write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=str(path.parent), prefix=path.name, suffix=".tmp", delete=False
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def build(repo_root: Path, output_dir: Path) -> dict[str, Path]:
    payloads = build_payloads(repo_root)
    written: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = output_dir / name
        write_json(path, payload)
        written[name] = path
    return written


def check(repo_root: Path, output_dir: Path) -> list[str]:
    payloads = build_payloads(repo_root)
    problems: list[str] = []
    for name, payload in payloads.items():
        path = output_dir / name
        expected = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if not path.is_file():
            problems.append(f"缺失: {path}")
            continue
        if path.read_text(encoding="utf-8") != expected:
            problems.append(f"内容不一致: {path}")
    return problems


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="只对比现有 fixtures，不写入")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    try:
        problems = check(repo_root, output_dir) if args.check else []
        if args.check:
            if problems:
                print("H05 fixtures 不一致：")
                for problem in problems:
                    print(f"- {problem}")
                return 1
            print(f"H05 fixtures 一致：{display_path(repo_root, output_dir)}")
            return 0
        written = build(repo_root, output_dir)
        for name, path in written.items():
            print(f"写出 {name}: {display_path(repo_root, path)}")
        print(f"H05 fixtures 完成：{len(written)} 个文件，{EXPECTED_BOOKS} 本 / {EXPECTED_PAGES} 页 / {EXPECTED_ASSETS} 个封面资源")
        return 0
    except FixtureError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
