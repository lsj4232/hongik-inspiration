#!/usr/bin/env python3
"""
홍익학당 영감노트 로컬 서버.

images/ 폴더 안의 하위 폴더를 자동으로 클러스터(테마)로 인식합니다.
- images/내가만든폴더1/  →  "내가만든폴더1" 클러스터
- images/foo.jpg          →  "미분류" 클러스터

사용법:
    python3 serve.py
    → http://localhost:8765/inspiration.html 접속
"""
import http.server
import socketserver
import json
import os
import sys
from urllib.parse import unquote
from pathlib import Path, PurePosixPath

PORT = 8765
ROOT = Path(__file__).parent.resolve()
IMG_DIR = ROOT / "images"
EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def build_manifest():
    """images/ 안을 스캔해 폴더별 클러스터를 만든다."""
    clusters = []
    if not IMG_DIR.exists():
        return {"clusters": []}

    # 하위 폴더(테마)
    subdirs = sorted([p for p in IMG_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")])
    for d in subdirs:
        imgs = sorted(
            [
                f"{d.name}/{f.name}"
                for f in d.rglob("*")
                if f.is_file() and f.suffix.lower() in EXTS
            ]
        )
        clusters.append({"id": d.name, "label": d.name, "images": imgs})

    # images/ 바로 아래 파일 = 미분류
    root_imgs = sorted(
        [
            f.name
            for f in IMG_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in EXTS
        ]
    )
    if root_imgs:
        clusters.append({"id": "_unclassified", "label": "미분류", "images": root_imgs})

    return {"clusters": clusters}


def _safe_resolve(rel_path: str) -> Path:
    """images/ 안으로 한정된 절대경로를 돌려준다. 벗어나면 ValueError."""
    if not rel_path or rel_path.startswith("/") or ".." in PurePosixPath(rel_path).parts:
        raise ValueError("invalid path")
    p = (IMG_DIR / rel_path).resolve()
    if IMG_DIR.resolve() not in p.parents and p != IMG_DIR.resolve():
        raise ValueError("outside images/")
    return p


def _validate_folder_name(name: str):
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("유효하지 않은 폴더명")
    if name.startswith("."):
        raise ValueError("폴더명은 .으로 시작할 수 없음")


def _unique_dest(directory: Path, basename: str) -> Path:
    """directory 안에서 충돌하지 않는 파일명을 찾는다."""
    dst = directory / basename
    if not dst.exists():
        return dst
    stem, ext = os.path.splitext(basename)
    i = 2
    while True:
        cand = directory / f"{stem}_{i}{ext}"
        if not cand.exists():
            return cand
        i += 1


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/manifest.json":
            return self._json(200, build_manifest())
        return super().do_GET()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_POST(self):
        try:
            if self.path == "/rename":
                return self._handle_rename()
            if self.path == "/move":
                return self._handle_move()
            if self.path == "/delete":
                return self._handle_delete()
            if self.path == "/folder/create":
                return self._handle_folder_create()
            if self.path == "/folder/rename":
                return self._handle_folder_rename()
            if self.path == "/folder/delete":
                return self._handle_folder_delete()
            return self._json(404, {"error": "not found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _handle_move(self):
        body = self._read_json()
        old_rel = body.get("old", "").strip()
        dest = body.get("dest", "").strip()  # "" 또는 "_unclassified" 이면 루트
        if not old_rel:
            return self._json(400, {"error": "old 필요"})
        src = _safe_resolve(old_rel)
        if not src.is_file():
            return self._json(404, {"error": "원본 파일 없음"})

        if dest and dest != "_unclassified":
            _validate_folder_name(dest)
            dest_dir = IMG_DIR / dest
            if not dest_dir.is_dir():
                return self._json(404, {"error": "대상 폴더가 없습니다"})
        else:
            dest_dir = IMG_DIR

        if src.parent.resolve() == dest_dir.resolve():
            return self._json(200, {"ok": True, "noop": True, "old": old_rel, "new": old_rel,
                                    "old_basename": src.name, "new_basename": src.name})

        dst = _unique_dest(dest_dir, src.name)
        src.rename(dst)
        new_rel = str(dst.relative_to(IMG_DIR)).replace(os.sep, "/")
        return self._json(200, {
            "ok": True,
            "old": old_rel,
            "new": new_rel,
            "old_basename": src.name,
            "new_basename": dst.name,
        })

    def _handle_rename(self):
        body = self._read_json()
        old_rel = body.get("old", "").strip()
        new_name = body.get("new_name", "").strip()
        if not old_rel or not new_name:
            return self._json(400, {"error": "old, new_name 필요"})
        if "/" in new_name or "\\" in new_name or new_name in {".", ".."}:
            return self._json(400, {"error": "파일명에 슬래시/특수경로 불가"})
        if new_name.startswith("."):
            return self._json(400, {"error": "파일명은 .으로 시작할 수 없음"})

        src = _safe_resolve(old_rel)
        if not src.is_file():
            return self._json(404, {"error": "원본 파일 없음"})
        if "." not in new_name:
            new_name = new_name + src.suffix
        dst = src.parent / new_name
        if dst.exists():
            return self._json(409, {"error": "같은 이름의 파일이 이미 있음"})
        src.rename(dst)
        new_rel = str(dst.relative_to(IMG_DIR)).replace(os.sep, "/")
        return self._json(200, {
            "ok": True,
            "old": old_rel,
            "new": new_rel,
            "old_basename": src.name,
            "new_basename": dst.name,
        })

    def _handle_delete(self):
        body = self._read_json()
        old_rel = body.get("old", "").strip()
        if not old_rel:
            return self._json(400, {"error": "old 필요"})
        src = _safe_resolve(old_rel)
        if not src.is_file():
            return self._json(404, {"error": "원본 파일 없음"})
        trash_dir = IMG_DIR / ".trash"
        trash_dir.mkdir(exist_ok=True)
        dst = _unique_dest(trash_dir, src.name)
        src.rename(dst)
        return self._json(200, {
            "ok": True,
            "trashed": str(dst.relative_to(IMG_DIR)).replace(os.sep, "/"),
            "old_basename": src.name,
        })

    def _handle_folder_create(self):
        body = self._read_json()
        name = body.get("name", "").strip()
        _validate_folder_name(name)
        target = IMG_DIR / name
        if target.exists():
            return self._json(409, {"error": "이미 같은 이름의 폴더/파일이 있음"})
        target.mkdir()
        return self._json(200, {"ok": True, "name": name})

    def _handle_folder_rename(self):
        body = self._read_json()
        old = body.get("old", "").strip()
        new = body.get("new", "").strip()
        _validate_folder_name(old)
        _validate_folder_name(new)
        src = IMG_DIR / old
        dst = IMG_DIR / new
        if not src.is_dir():
            return self._json(404, {"error": "폴더가 없습니다"})
        if dst.exists():
            return self._json(409, {"error": "이미 같은 이름이 있음"})
        src.rename(dst)
        return self._json(200, {"ok": True, "old": old, "new": new})

    def _handle_folder_delete(self):
        body = self._read_json()
        name = body.get("name", "").strip()
        move_contents = bool(body.get("move_contents", False))
        _validate_folder_name(name)
        target = IMG_DIR / name
        if not target.is_dir():
            return self._json(404, {"error": "폴더가 없습니다"})

        files = [f for f in target.iterdir() if f.is_file()]
        subdirs = [f for f in target.iterdir() if f.is_dir()]
        if subdirs:
            return self._json(409, {"error": "하위 폴더가 있어 삭제할 수 없습니다"})
        if files and not move_contents:
            return self._json(409, {"error": f"폴더에 {len(files)}개의 파일이 있음", "count": len(files)})

        renames = []
        for f in files:
            dst = _unique_dest(IMG_DIR, f.name)
            f.rename(dst)
            if f.name != dst.name:
                renames.append({"old_basename": f.name, "new_basename": dst.name})

        # 그래도 남은 항목 있으면 거부
        if any(target.iterdir()):
            return self._json(409, {"error": "폴더에 다른 항목이 남아있음"})
        target.rmdir()
        return self._json(200, {"ok": True, "moved": len(files), "renames": renames})

    def log_message(self, fmt, *args):
        # 조용히
        pass


def main():
    os.chdir(ROOT)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/inspiration.html"
        print(f"\n  영감노트 서버 실행 중")
        print(f"  → {url}")
        print(f"  중단: Ctrl+C\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  종료")


if __name__ == "__main__":
    main()
