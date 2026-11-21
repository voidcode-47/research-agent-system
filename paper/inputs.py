"""Input identities for queued operations and reusable rewrite guides."""
import hashlib
import json

from paper import store


def fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def requirement_fingerprint(requirement: dict) -> str:
    return fingerprint({k: v for k, v in requirement.items() if k != "updated_at"})


def capture(paper_id: str, llm) -> dict:
    with store.locked():
        version, parsed = store.current_snapshot(paper_id)
        requirement = store.load_requirement(paper_id) or {}
        guide = store.load_rewrite_guide(paper_id)
    model = {name: getattr(llm, name, "") for name in ("provider", "model", "base_url", "protocol", "api_key")}
    result = {
        "version": version, "parsed": parsed,
        "paragraphs": parsed.get("paragraphs") or [],
        "requirement": requirement, "guide": guide,
        "body_fingerprint": fingerprint(parsed),
        "requirement_fingerprint": requirement_fingerprint(requirement),
        "model_fingerprint": fingerprint(model),
    }
    result["fingerprint"] = fingerprint({k: v for k, v in result.items() if k not in ("requirement", "parsed", "paragraphs")})
    result["llm"] = llm
    return result


def matches(paper_id: str, inputs: dict) -> bool:
    with store.locked():
        try:
            version, parsed = store.current_snapshot(paper_id)
            return (version == inputs["version"]
                    and requirement_fingerprint(store.load_requirement(paper_id) or {}) == inputs["requirement_fingerprint"]
                    and fingerprint(parsed) == inputs["body_fingerprint"])
        except FileNotFoundError:
            return False


def guide_current(guide: dict | None, inputs: dict) -> bool:
    return bool(guide and not guide.get("stale")
                and guide.get("version") == inputs["version"]
                and guide.get("requirement_fingerprint") == inputs["requirement_fingerprint"]
                and guide.get("body_fingerprint") == inputs["body_fingerprint"])


def usable_guide(inputs: dict, requested: bool) -> dict | None:
    guide = inputs.get("guide") if requested else None
    if guide and not guide_current(guide, inputs):
        raise ValueError("全局改写方针已过期，请重新生成，或关闭「套用全局方针」后重试")
    return guide
