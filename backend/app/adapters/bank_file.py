import hashlib
import re
from pathlib import Path

from app.config import settings

"""BankFileAdapter —— 浙江农信原始文件安全落盘（规格 1.3 / 8.1 / 10）。

- 原始文件长期保存、只读、不覆盖：同名自动 version 递增
- SHA256 指纹
- 路径: {DATA_DIR}/finance/{company}/{YYYY}/{MM}/original/bank/
"""

SAFE_NAME = re.compile(r"[^\w.\-一-龥]+")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_name(name: str) -> str:
    clean = SAFE_NAME.sub("_", name.replace("..", "_"))
    clean = clean.lstrip(".")[:180] or "unnamed"
    return clean


class BankFileAdapter:
    provider = "zhejiang_rural_credit"

    ALLOWED_EXT = {".xlsx", ".xls", ".pdf", ".zip"}

    def save_original(
        self,
        company: str,
        period_year: int,
        period_month: int,
        category: str,
        original_name: str,
        content: bytes,
    ) -> dict:
        ext = Path(original_name).suffix.lower()
        if ext not in self.ALLOWED_EXT:
            raise ValueError(f"不支持的文件类型: {ext}（允许 XLSX/PDF/ZIP）")

        base_dir = (
            Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
            / f"{period_year:04d}" / f"{period_month:02d}" / "original" / category
        )
        base_dir.mkdir(parents=True, exist_ok=True)

        clean = sanitize_name(original_name)
        # 同名不覆盖：version 递增
        version = 1
        target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        while target.exists():
            version += 1
            target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        target.write_bytes(content)

        return {
            "stored_path": str(target),
            "original_name": original_name,
            "category": category,
            "size": len(content),
            "sha256": sha256_of(target),
            "version": version,
        }
