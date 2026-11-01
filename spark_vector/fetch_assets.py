"""
Build-time asset fetcher for spark-vector (design doc §4).

Downloads the pinned ONNX model + tokenizer files from Hugging Face during
`docker build`, verifies the model checksum, and writes a MANIFEST.json —
so the container never touches the network at boot ("weights baked into the
image at build time: pinned revision + checksum").

Served model: int8-quantized export of intfloat/multilingual-e5-base (XLM-R,
768-dim), served from Xenova/multilingual-e5-base which carries portable
dynamic-int8 ONNX (no CPU-specific VNNI instructions required).
"""

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

# Files required to serve embeddings fully offline.
ASSETS = [
    "onnx/model_int8.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
]

# Source-of-truth model (provenance record only; bytes come from the pinned export above).
BASE_MODEL = "intfloat/multilingual-e5-base"
BASE_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"

USER_AGENT = "spark-vector-build/1.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(repo: str, revision: str, rel_path: str, dest: Path) -> str:
    """Download one file at an immutable commit pin; return its sha256."""
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{rel_path}"
    print(f"[fetch] {rel_path} <- {url}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out)
    return sha256(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--revision", required=True, help="pinned commit hash")
    parser.add_argument("--sha256", required=True, help="expected sha256 of onnx/model_int8.onnx")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = Path(args.out)
    manifest = {
        "repo": args.repo,
        "revision": args.revision,
        "base_model": BASE_MODEL,
        "base_revision": BASE_REVISION,
        "files": [],
    }

    for rel_path in ASSETS:
        dest = out / rel_path
        digest = fetch(args.repo, args.revision, rel_path, dest)
        if rel_path == "onnx/model_int8.onnx" and digest != args.sha256:
            print(
                f"[fetch] CHECKSUM MISMATCH for {rel_path}: got {digest}, want {args.sha256}",
                file=sys.stderr,
            )
            return 1
        manifest["files"].append({"path": rel_path, "sha256": digest})

    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print(f"[fetch] ok: {len(ASSETS)} files baked -> {out} (revision {args.revision})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
