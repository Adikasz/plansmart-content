"""Muapi account/state debug — miért jönnek PÉLDA-képek valódi generálás helyett?

1) GET /api/v1/account/balance            — teljes JSON
2) POST /api/v1/flux-schnell (minimal)     — teljes request + response, poll
3) 3x ismételt submit                      — ugyanaz a request_id? (sandbox/cache jele)

Futtatás:
    python scripts/debug_muapi.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

BASE = "https://api.muapi.ai/api/v1"
KEY = os.environ.get("MUAPI_API_KEY", "")
H = {"x-api-key": KEY, "Content-Type": "application/json"}
PLACEHOLDER_MARKER = "/webassets/"


def p(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


async def get_balance(c: httpx.AsyncClient, title: str = "1) GET /account/balance") -> float | None:
    p(title)
    r = await c.get(f"{BASE}/account/balance", headers=H, timeout=20)
    print("HTTP", r.status_code)
    try:
        data = r.json()
        dump(data)
        return data.get("balance")
    except Exception:
        print(r.text[:500])
        return None


async def get_schema(c: httpx.AsyncClient, model: str) -> dict:
    r = await c.get(f"{BASE}/models/{model}", headers=H, timeout=20)
    if r.status_code >= 400:
        return {}
    return r.json().get("input_schema", {}).get("schemas", {}).get("input_data", {})


def build_body(schema: dict, base_body: dict) -> dict:
    """A kért base_body + a séma szerinti hiányzó kötelező/defaultos enumok kitöltése."""
    body = dict(base_body)
    props = schema.get("properties", {})
    for name, spec in props.items():
        if name in body or name == "prompt":
            continue
        if "enum" in spec:
            body[name] = spec.get("default", spec["enum"][0])
        elif "default" in spec and spec["default"] is not None:
            body[name] = spec["default"]
    return body


async def submit(c: httpx.AsyncClient, model: str, body: dict) -> dict:
    r = await c.post(f"{BASE}/{model}", json=body, headers=H, timeout=40)
    out = {"http": r.status_code, "json": None, "text": None}
    try:
        out["json"] = r.json()
    except Exception:
        out["text"] = r.text[:500]
    return out


async def poll(c: httpx.AsyncClient, rid: str, tries: int = 30) -> dict:
    last = {}
    for i in range(tries):
        r = await c.get(f"{BASE}/predictions/{rid}/result", headers=H, timeout=30)
        last = r.json()
        st = str(last.get("status", "")).lower()
        if st in {"completed", "succeeded", "success", "failed", "error"}:
            last["_polls"] = i + 1
            return last
        await asyncio.sleep(2)
    last["_polls"] = tries
    return last


def first_image(data: dict) -> str | None:
    for k in ("images", "outputs"):
        v = data.get(k)
        if isinstance(v, list) and v:
            return v[0]
    return None


async def _try_model(c: httpx.AsyncClient, model: str, requested: dict) -> tuple[str | None, str | None, dict]:
    """Egy submit+poll: visszaadja (request_id, image_url, full_submit_response)."""
    schema = await get_schema(c, model)
    body = build_body(schema, requested) if schema else dict(requested)
    res = await submit(c, model, body)
    rid = (res["json"] or {}).get("request_id") or (res["json"] or {}).get("id")
    img = None
    if rid:
        img = first_image(await poll(c, rid))
    return rid, img, {"body": body, "submit": res}


async def test_generation(c: httpx.AsyncClient) -> None:
    p("2) Minimal test generation")
    requested = {"prompt": "a red circle on black background", "resolution": "1k"}

    # 2a) A kért flux-schnell — megmutatjuk, hogy ez 404-et ad ezen az accounton.
    model = "flux-schnell"
    schema = await get_schema(c, model)
    print(f"--- {model} input séma paraméterek:")
    for name, spec in (schema.get("properties") or {}).items():
        print(f"  - {name}: {spec.get('enum') or spec.get('type')} (default={spec.get('default')})")
    body = build_body(schema, requested) if schema else dict(requested)
    print(f"\nREQUEST:  POST {BASE}/{model}")
    print(f"  headers: {{'x-api-key': '***{KEY[-4:] if KEY else ''}', 'Content-Type': 'application/json'}}")
    print("  body:", json.dumps(body, ensure_ascii=False))
    res = await submit(c, model, body)
    print("RESPONSE (submit):  HTTP", res["http"])
    dump(res["json"] if res["json"] is not None else res["text"])
    if res["http"] == 404:
        print(f"⇒ A /{model} végpont 404 ezen az accounton — nem hívható. Áttérünk flux-2-pro-ra.")

    # 2b) flux-2-pro — ez ELFOGADJA a POST-ot (request_id-t ad), így ezen látszik az igazi viselkedés.
    model = "flux-2-pro"
    rid, img, info = await _try_model(c, model, requested)
    print(f"\n--- {model} (ténylegesen hívható végpont)")
    print(f"REQUEST:  POST {BASE}/{model}")
    print("  body:", json.dumps(info["body"], ensure_ascii=False))
    print("RESPONSE (submit):  HTTP", info["submit"]["http"])
    dump(info["submit"]["json"] if info["submit"]["json"] is not None else info["submit"]["text"])
    print("\nrequest_id:", rid)
    print("Kép URL:", img)
    print("Placeholder (példa-kép)?", bool(img and PLACEHOLDER_MARKER in img))


async def repeat_check(c: httpx.AsyncClient) -> None:
    model = "flux-2-pro"  # flux-schnell 404 → flux-2-pro-n nézzük az ismételhetőséget
    p(f"3) 3x ismételt submit ({model}) — egyedi request_id? cache/sandbox?")
    requested = {"prompt": "a red circle on black background", "resolution": "1k"}

    rids, imgs = [], []
    for i in range(3):
        rid, img, _ = await _try_model(c, model, requested)
        rids.append(rid)
        imgs.append(img)
        print(f"  #{i+1}: request_id={rid}  img={img}")

    uniq_rids = len(set(rids)) == len(rids) and all(rids)
    all_placeholder = bool(imgs) and all(i and PLACEHOLDER_MARKER in i for i in imgs)
    print("\nrequest_id-k egyediek?", uniq_rids, "->", rids)
    print("minden kép placeholder?", all_placeholder)
    print("kép URL-ek azonosak?", len(set(imgs)) == 1, "->", imgs)

    if not any(rids):
        print("⇒ Egyik submit sem adott request_id-t — a végpont nem hívható.")
    elif not uniq_rids and len(set(rids)) == 1:
        print("⇒ MINDIG ugyanaz a request_id: cache/sandbox, nem valódi generálás.")
    elif all_placeholder:
        print("⇒ EGYEDI request_id-k, de MIND PÉLDA-kép ($0): a job nem fut le igazából.")
        print("   A request helyes; ez account-oldali állapot (web_safety_forced / aktiválás).")
    else:
        print("⇒ Úgy tűnik, valódi (egyedi) képek jönnek.")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not KEY:
        print("HIBA: MUAPI_API_KEY hiányzik a .env-ből.")
        return 1
    async with httpx.AsyncClient() as c:
        before = await get_balance(c)
        await test_generation(c)
        await repeat_check(c)
        after = await get_balance(c, "4) GET /account/balance (a tesztek UTÁN)")
        if before is not None and after is not None:
            print(f"\nEgyenleg változás a teszt-submitek alatt: {before} -> {after} (Δ={after - before:+.4f} USD)")
            print("Ha Δ=0: az API submitek NEM kerültek pénzbe ⇒ a generálás nem futott le (csak a playground számláz).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
