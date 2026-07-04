from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())
AUTH = {"Authorization": "Bearer kp_testtest_secret"}


def test_search_requires_auth():
    resp = client.post("/v1/search", json={"query": "发票"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_search_validation_maps_to_400_envelope():
    # 契约：参数校验失败返回 400 invalid_argument，而非 FastAPI 默认 422（技术设计文档 6.1）
    resp = client.post("/v1/search", json={"query": ""}, headers=AUTH)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_argument"
    assert body["error"]["request_id"].startswith("req_")

    resp = client.post("/v1/search", json={"query": "发票", "type": ["wiki"]}, headers=AUTH)
    assert resp.status_code == 400

    resp = client.post("/v1/search", json={"query": "发票", "top_k": 21}, headers=AUTH)
    assert resp.status_code == 400


# 参数合法 + 未知 key → 401 的用例在 test_gateway.py（需要 DB，走依赖覆盖的 async client）


def test_request_id_header_present():
    resp = client.post("/v1/search", json={"query": "发票"}, headers=AUTH)
    assert resp.headers.get("X-Request-Id", "").startswith("req_")
