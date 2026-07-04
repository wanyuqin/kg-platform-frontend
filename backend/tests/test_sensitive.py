from app.pipeline.sensitive import scan


def test_mobile_number_hit():
    hits = scan("联系人电话 13812345678，工作日回复")
    assert any(h.rule == "手机号" for h in hits)


def test_mobile_number_not_hit_inside_longer_digits():
    assert scan("订单号 913812345678001") == []


def test_id_card_hit():
    assert any(h.rule == "身份证号" for h in scan("身份证 11010119900101003X"))


def test_secret_assignment_hit():
    assert any(h.rule == "Secret 赋值" for h in scan("api_key = sk-abcdef123456"))


def test_internal_ip_hit():
    assert any(h.rule == "内网 IP" for h in scan("服务部署在 172.20.3.11"))


def test_clean_content():
    assert scan("企业版发票 1-3 个工作日开出，专票 5-7 个工作日邮寄。") == []
