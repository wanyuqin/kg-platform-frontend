"""控制台接口（技术设计文档 七，P1 清单）。

登录（7.1 飞书 OAuth）与各接口逐个实现；未实现的先占位 501，
保证前端联调时拿到统一错误 envelope 而不是 404。
"""

from fastapi import APIRouter

from app import errors

router = APIRouter(prefix="/api", tags=["console"])

_PENDING = [
    ("GET", "/auth/login"),
    ("GET", "/auth/callback"),
    ("POST", "/domains"),
    ("GET", "/domains"),
    ("PATCH", "/domains/{code}"),
    ("POST", "/domains/{code}/members"),
    ("DELETE", "/domains/{code}/members"),
    ("POST", "/domains/{code}/keys"),
    ("DELETE", "/keys/{key_id}"),
    ("GET", "/knowledge"),
    ("GET", "/knowledge/{kid}"),
    ("POST", "/knowledge"),
    ("PUT", "/knowledge/{kid}"),
    ("PATCH", "/knowledge/{kid}/meta"),
    ("POST", "/knowledge/{kid}/archive"),
    ("POST", "/knowledge/{kid}/renew"),
    ("POST", "/imports"),
    ("GET", "/imports/{batch_id}"),
    ("POST", "/imports/{batch_id}/confirm"),
    ("GET", "/templates/{type}.md"),
    ("GET", "/audit-logs"),
    ("GET", "/audit-logs/export"),
]


def _make_stub(method: str, path: str):
    async def stub():
        raise errors.not_implemented(f"{method} /api{path}")

    return stub


for _method, _path in _PENDING:
    router.add_api_route(_path, _make_stub(_method, _path), methods=[_method])
