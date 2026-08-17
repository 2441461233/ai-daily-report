#!/usr/bin/env python3
"""Validate Artificial Analysis input, diff, snapshot, and daily attachment."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
SOURCE_URL = "https://artificialanalysis.ai/leaderboards/models/"
METHODOLOGY_URL = "https://artificialanalysis.ai/methodology/intelligence-benchmarking"
METRIC = "Artificial Analysis Intelligence Index"
TOP_LIMIT = 10
ATTACHMENT_RE = re.compile(
    r"^content/artifacts/artificial-analysis-(\d{8})-(\d{6})\.json$"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_INPUT_AGE = timedelta(hours=2)
MAX_CLOCK_SKEW = timedelta(minutes=5)


class Errors:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, location: str, message: str) -> None:
        self.messages.append(f"{location}: {message}")


def load_json(path: Path, errors: Errors, location: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.add(location, f"required file does not exist: {path}")
    except json.JSONDecodeError as exc:
        errors.add(location, f"invalid JSON at {exc.lineno}:{exc.colno}: {exc.msg}")
    except (OSError, UnicodeError) as exc:
        errors.add(location, f"cannot read UTF-8 JSON: {exc}")
    return None


def parse_timestamp(value: Any, location: str, errors: Errors) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        errors.add(location, "must be a non-empty ISO-8601 timestamp")
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        errors.add(location, "must be a valid ISO-8601 timestamp")
        return None
    if parsed.utcoffset() is None:
        errors.add(location, "must include an explicit timezone offset")
        return None
    return parsed.astimezone(timezone.utc)


def round_two(value: float) -> float | int:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    result = float(rounded)
    return int(result) if result.is_integer() else result


def score_text(value: float | int) -> str:
    rendered = f"{float(value):.2f}"
    return rendered[:-3] if rendered.endswith(".00") else rendered


def model_url(slug: str) -> str:
    return f"https://artificialanalysis.ai/models/{quote(slug, safe='')}"


def normalize_model(
    value: Any, index: int, location: str, errors: Errors
) -> Optional[dict[str, Any]]:
    item_location = f"{location}.models[{index}]"
    if not isinstance(value, dict):
        errors.add(item_location, "must be a JSON object")
        return None
    rank = value.get("rank")
    if isinstance(rank, bool) or rank != index + 1:
        errors.add(f"{item_location}.rank", f"must be exactly {index + 1}")
    slug = value.get("slug")
    name = value.get("name")
    creator = value.get("creator")
    if not isinstance(slug, str) or not slug.strip() or re.search(r"[\s/]", slug):
        errors.add(f"{item_location}.slug", "must be a non-empty canonical model slug")
        return None
    slug = slug.strip()
    if not isinstance(name, str) or not name.strip():
        errors.add(f"{item_location}.name", "must be a non-empty string")
        return None
    name = name.strip()
    if creator is not None and (not isinstance(creator, str) or not creator.strip()):
        errors.add(f"{item_location}.creator", "must be null or a non-empty string")
        creator = None
    elif isinstance(creator, str):
        creator = creator.strip()
    score = value.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        errors.add(f"{item_location}.score", "must be a finite number")
        return None
    estimated = value.get("estimated")
    if type(estimated) is not bool:
        errors.add(f"{item_location}.estimated", "must be a boolean")
        estimated = False
    expected_url = model_url(slug)
    if value.get("url") != expected_url:
        errors.add(f"{item_location}.url", f"must be exactly {expected_url!r}")
    return {
        "rank": index + 1,
        "slug": slug,
        "name": name,
        "creator": creator,
        "score": round_two(float(score)),
        "estimated": estimated,
        "url": expected_url,
    }


def normalize_snapshot(
    value: Any, location: str, errors: Errors
) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        errors.add(location, "must be a JSON object")
        return None
    if value.get("schemaVersion") != 1:
        errors.add(f"{location}.schemaVersion", "must be exactly 1")
    if value.get("sourceUrl") != SOURCE_URL:
        errors.add(f"{location}.sourceUrl", f"must be exactly {SOURCE_URL!r}")
    if value.get("metric") != METRIC:
        errors.add(f"{location}.metric", f"must be exactly {METRIC!r}")
    methodology_version = value.get("methodologyVersion")
    if methodology_version is not None and (
        not isinstance(methodology_version, str)
        or re.fullmatch(r"\d+\.\d+(?:\.\d+)?", methodology_version) is None
    ):
        errors.add(
            f"{location}.methodologyVersion",
            "must be null or a dotted numeric version",
        )
        methodology_version = None
    if value.get("limit") != TOP_LIMIT:
        errors.add(f"{location}.limit", f"must be exactly {TOP_LIMIT}")
    models_value = value.get("models")
    if not isinstance(models_value, list) or len(models_value) != TOP_LIMIT:
        found = len(models_value) if isinstance(models_value, list) else "non-array"
        errors.add(f"{location}.models", f"must contain exactly {TOP_LIMIT} models, found {found}")
        return None
    models: list[dict[str, Any]] = []
    for index, model_value in enumerate(models_value):
        model = normalize_model(model_value, index, location, errors)
        if model is not None:
            models.append(model)
    if len(models) != TOP_LIMIT:
        return None
    slugs = [model["slug"] for model in models]
    if len(slugs) != len(set(slugs)):
        errors.add(f"{location}.models", "model slugs must be unique")
    for index in range(1, len(models)):
        if models[index]["score"] > models[index - 1]["score"]:
            errors.add(f"{location}.models[{index}]", "scores must be in descending order")
    return {
        "schemaVersion": 1,
        "sourceUrl": SOURCE_URL,
        "metric": METRIC,
        "methodologyVersion": methodology_version,
        "limit": TOP_LIMIT,
        "models": models,
    }


def expected_changes(
    previous: Optional[dict[str, Any]], current: dict[str, Any]
) -> list[dict[str, Any]]:
    if previous is None:
        return []
    old_by_slug = {model["slug"]: model for model in previous["models"]}
    new_by_slug = {model["slug"]: model for model in current["models"]}
    changes: list[dict[str, Any]] = []
    if previous["methodologyVersion"] != current["methodologyVersion"]:
        changes.append(
            {
                "type": "methodology_changed",
                "previousVersion": previous["methodologyVersion"],
                "currentVersion": current["methodologyVersion"],
            }
        )
    for model in current["models"]:
        if model["slug"] not in old_by_slug:
            changes.append(
                {
                    "type": "entered_top_10",
                    "slug": model["slug"],
                    "name": model["name"],
                    "creator": model["creator"],
                    "currentRank": model["rank"],
                    "currentScore": model["score"],
                }
            )
    for model in previous["models"]:
        if model["slug"] not in new_by_slug:
            changes.append(
                {
                    "type": "exited_top_10",
                    "slug": model["slug"],
                    "name": model["name"],
                    "creator": model["creator"],
                    "previousRank": model["rank"],
                    "previousScore": model["score"],
                }
            )
    for model in current["models"]:
        old = old_by_slug.get(model["slug"])
        if old is None or old["rank"] == model["rank"]:
            continue
        delta = old["rank"] - model["rank"]
        changes.append(
            {
                "type": "rank_changed",
                "slug": model["slug"],
                "name": model["name"],
                "creator": model["creator"],
                "previousRank": old["rank"],
                "currentRank": model["rank"],
                "rankDelta": delta,
                "direction": "up" if delta > 0 else "down",
            }
        )
    for model in current["models"]:
        old = old_by_slug.get(model["slug"])
        if old is None or old["score"] == model["score"]:
            continue
        delta = round_two(float(model["score"]) - float(old["score"]))
        changes.append(
            {
                "type": "score_changed",
                "slug": model["slug"],
                "name": model["name"],
                "creator": model["creator"],
                "currentRank": model["rank"],
                "previousScore": old["score"],
                "currentScore": model["score"],
                "scoreDelta": delta,
                "direction": "up" if delta > 0 else "down",
            }
        )
    metadata_fields = ("name", "creator", "estimated")
    for model in current["models"]:
        old = old_by_slug.get(model["slug"])
        if old is None:
            continue
        changed_fields = [
            field for field in metadata_fields if old[field] != model[field]
        ]
        if not changed_fields:
            continue
        changes.append(
            {
                "type": "metadata_changed",
                "slug": model["slug"],
                "name": model["name"],
                "creator": model["creator"],
                "currentRank": model["rank"],
                "changedFields": changed_fields,
                "previousMetadata": {
                    "name": old["name"],
                    "creator": old["creator"],
                    "estimated": old["estimated"],
                },
                "currentMetadata": {
                    "name": model["name"],
                    "creator": model["creator"],
                    "estimated": model["estimated"],
                },
            }
        )
    return changes


def artifact_item(change: dict[str, Any]) -> dict[str, Any]:
    shared = {
        "expanded": False,
        "sources": [{"name": "Artificial Analysis（单一来源）", "url": SOURCE_URL}],
    }
    if change["type"] == "methodology_changed":
        previous = (
            "未记录"
            if change["previousVersion"] is None
            else f"v{change['previousVersion']}"
        )
        current = (
            "未记录"
            if change["currentVersion"] is None
            else f"v{change['currentVersion']}"
        )
        return {
            "headline": f"评测方法更新：Intelligence Index {previous} → {current}",
            "summary": f"{METRIC} 的公开方法版本由 {previous} 更新为 {current}；跨版本分数和名次需结合方法调整解读。",
            "expanded": False,
            "sources": [
                {"name": "Artificial Analysis（单一来源）", "url": METHODOLOGY_URL}
            ],
        }
    if change["type"] == "entered_top_10":
        return {
            "headline": f"新进 Top 10：{change['name']} 升至第 {change['currentRank']} 名",
            "summary": f"{change['name']} 新进入 {METRIC} 前十，当前第 {change['currentRank']} 名，得分 {score_text(change['currentScore'])}。",
            **shared,
        }
    if change["type"] == "exited_top_10":
        return {
            "headline": f"退出 Top 10：{change['name']}",
            "summary": f"{change['name']} 退出 {METRIC} 前十；上一快照为第 {change['previousRank']} 名，得分 {score_text(change['previousScore'])}。",
            **shared,
        }
    if change["type"] == "rank_changed":
        movement = "上升" if change["direction"] == "up" else "下降"
        return {
            "headline": f"排名{movement}：{change['name']} 第 {change['previousRank']} → {change['currentRank']} 名",
            "summary": f"{change['name']} 在 {METRIC} 中{movement} {abs(change['rankDelta'])} 位，由第 {change['previousRank']} 名变为第 {change['currentRank']} 名。",
            **shared,
        }
    if change["type"] == "score_changed":
        sign = "+" if change["scoreDelta"] > 0 else ""
        return {
            "headline": f"分数变化：{change['name']} {score_text(change['previousScore'])} → {score_text(change['currentScore'])}",
            "summary": f"{change['name']} 当前位列第 {change['currentRank']}，Intelligence Index 得分变化 {sign}{score_text(change['scoreDelta'])}，由 {score_text(change['previousScore'])} 变为 {score_text(change['currentScore'])}。",
            **shared,
        }
    if change["type"] == "metadata_changed":
        descriptions: list[str] = []
        for field in change["changedFields"]:
            if field == "name":
                descriptions.append(
                    f"名称“{change['previousMetadata']['name']}”→“{change['currentMetadata']['name']}”"
                )
            elif field == "creator":
                previous_creator = change["previousMetadata"]["creator"] or "未标注"
                current_creator = change["currentMetadata"]["creator"] or "未标注"
                descriptions.append(
                    f"开发者“{previous_creator}”→“{current_creator}”"
                )
            else:
                previous_label = (
                    "估算分" if change["previousMetadata"]["estimated"] else "正式分"
                )
                current_label = (
                    "估算分" if change["currentMetadata"]["estimated"] else "正式分"
                )
                descriptions.append(
                    f"分数标记“{previous_label}”→“{current_label}”"
                )
        return {
            "headline": f"榜单信息更新：{change['name']}",
            "summary": f"{change['name']} 当前位列第 {change['currentRank']}，官方榜单元数据更新：{'；'.join(descriptions)}。",
            **shared,
        }
    raise ValueError(f"unsupported change type {change.get('type')!r}")


def shanghai_timestamp(value: datetime) -> str:
    return value.astimezone(SHANGHAI).isoformat(timespec="seconds")


def expected_artifact(
    report_date: str,
    generated_at: datetime,
    current: dict[str, Any],
    changes: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not changes:
        return None
    leader = current["models"][0]
    stamp = report_date.replace("-", "")
    time_stamp = generated_at.astimezone(SHANGHAI).strftime("%H%M%S")
    return {
        "path": f"content/artifacts/artificial-analysis-{stamp}-{time_stamp}.json",
        "document": {
            "date": report_date,
            "label": "Artificial Analysis 排名变化",
            "attachTo": report_date,
            "generatedAt": shanghai_timestamp(generated_at),
            "sections": [
                {
                    "title": "📊 Artificial Analysis 模型排名",
                    "note": f"与上一份快照相比发现 {len(changes)} 项变化；当前榜首为 {leader['name']}（{score_text(leader['score'])} 分）。",
                    "items": [artifact_item(change) for change in changes],
                }
            ],
        },
    }


def read_manifest(path: Path, errors: Errors) -> list[str]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.add("manifest", f"cannot read {path}: {exc}")
        return []
    if any(not line.strip() or line != line.strip() for line in raw_lines):
        errors.add("manifest", "must contain one clean relative path per line")
    lines = [line.strip() for line in raw_lines if line.strip()]
    if len(lines) != len(set(lines)):
        errors.add("manifest", "must not contain duplicate paths")
    for line in lines:
        if ATTACHMENT_RE.fullmatch(line) is None:
            errors.add("manifest", f"unexpected path {line!r}")
    return lines


def validate_run(
    *,
    input_path: Path,
    manifest_path: Path,
    snapshot_path: Path,
    snapshot_role: str,
    report_date: str,
    now: datetime,
    root: Path,
) -> list[str]:
    errors = Errors()
    document = load_json(input_path, errors, "input")
    if not isinstance(document, dict):
        if document is not None:
            errors.add("input", "must be a JSON object")
        return errors.messages
    if document.get("schemaVersion") != 1:
        errors.add("$.schemaVersion", "must be exactly 1")
    if document.get("reportDate") != report_date:
        errors.add("$.reportDate", f"must be exactly {report_date!r}")
    generated_at = parse_timestamp(document.get("generatedAt"), "$.generatedAt", errors)
    if generated_at is not None:
        if generated_at > now + MAX_CLOCK_SKEW:
            errors.add("$.generatedAt", "is more than five minutes in the future")
        elif now - generated_at > MAX_INPUT_AGE:
            errors.add("$.generatedAt", "is more than two hours old")
        generated_date = generated_at.astimezone(SHANGHAI).date().isoformat()
        if generated_date != report_date:
            errors.add(
                "$.generatedAt",
                f"Shanghai date is {generated_date}, expected {report_date}",
            )

    expected_source = {
        "id": "artificial-analysis-models",
        "name": "Artificial Analysis LLM Leaderboard",
        "url": SOURCE_URL,
        "methodologyUrl": METHODOLOGY_URL,
        "metric": METRIC,
        "method": "official_public_ssr_table",
    }
    if document.get("source") != expected_source:
        errors.add("$.source", "must exactly describe the official public SSR leaderboard")

    previous_value = document.get("previousSnapshot")
    previous = None
    if previous_value is not None:
        previous = normalize_snapshot(previous_value, "$.previousSnapshot", errors)
        if previous is not None and previous_value != previous:
            errors.add("$.previousSnapshot", "must use the canonical snapshot shape")
    current = normalize_snapshot(document.get("currentSnapshot"), "$.currentSnapshot", errors)
    if current is not None and document.get("currentSnapshot") != current:
        errors.add("$.currentSnapshot", "must use the canonical snapshot shape")
    if document.get("previous") != previous_value:
        errors.add("$.previous", "must exactly equal previousSnapshot")
    if document.get("current") != document.get("currentSnapshot"):
        errors.add("$.current", "must exactly equal currentSnapshot")

    changes_value = document.get("changes")
    if not isinstance(changes_value, list):
        errors.add("$.changes", "must be an array")
        changes_value = []
    changes: list[dict[str, Any]] = []
    if current is not None:
        changes = expected_changes(previous, current)
        if changes_value != changes:
            errors.add("$.changes", "does not exactly match the deterministic snapshot diff")
    expected_status = "baseline" if previous is None else "changed" if changes else "unchanged"
    if document.get("status") != expected_status:
        errors.add("$.status", f"must be exactly {expected_status!r}")

    expected_attachment = (
        expected_artifact(report_date, generated_at, current, changes)
        if generated_at is not None and current is not None
        else None
    )
    if document.get("artifact") != expected_attachment:
        errors.add("$.artifact", "does not exactly match the deterministic diff attachment")

    manifest = read_manifest(manifest_path, errors)
    expected_manifest = [expected_attachment["path"]] if expected_attachment else []
    if manifest != expected_manifest:
        errors.add("manifest", f"must be exactly {expected_manifest!r}, found {manifest!r}")
    if expected_attachment is not None:
        attachment_path = root / expected_attachment["path"]
        actual_attachment = load_json(attachment_path, errors, expected_attachment["path"])
        if actual_attachment != expected_attachment["document"]:
            errors.add(
                expected_attachment["path"],
                "must exactly equal the trusted collector attachment document",
            )

    expected_snapshot = previous_value if snapshot_role == "previous" else document.get("currentSnapshot")
    if expected_snapshot is None and not snapshot_path.exists():
        snapshot_document = None
    else:
        snapshot_document = load_json(snapshot_path, errors, "snapshot")
        if snapshot_document != expected_snapshot:
            errors.add(
                "snapshot",
                f"must exactly equal {snapshot_role}Snapshot from the trusted input",
            )
    return errors.messages


def parse_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an exact YYYY-MM-DD calendar date") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("must be an exact YYYY-MM-DD calendar date")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("/tmp/artificial-analysis.json"))
    parser.add_argument(
        "--changed-manifest",
        type=Path,
        default=Path("/tmp/artificial-analysis.changed"),
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "content" / "artificial-analysis-snapshot.json",
    )
    parser.add_argument("--snapshot-role", choices=("previous", "current"), default="previous")
    parser.add_argument("--date", type=parse_date, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    timestamp_errors = Errors()
    now = parse_timestamp(arguments.now, "--now", timestamp_errors)
    if now is None:
        for message in timestamp_errors.messages:
            print(message, file=sys.stderr)
        return 1
    messages = validate_run(
        input_path=arguments.input,
        manifest_path=arguments.changed_manifest,
        snapshot_path=arguments.snapshot,
        snapshot_role=arguments.snapshot_role,
        report_date=arguments.date,
        now=now,
        root=arguments.root,
    )
    if messages:
        print("Artificial Analysis run validation failed:", file=sys.stderr)
        for message in messages:
            print(f"  - {message}", file=sys.stderr)
        return 1
    document = json.loads(arguments.input.read_text(encoding="utf-8"))
    print(
        "Artificial Analysis run validation passed: "
        f"{document['status']}, {len(document['changes'])} change(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
