"""進場分數：起漲給高分、漲一波和假量給低分。"""
from radar import entry as E


def base(**kw):
    m = {"bias": 4.0, "ret20": 6.0, "vr": 4.0, "close_pos": 0.95, "chg": 6.0, "amp": 7.0,
         "power": 1500.0, "pre_bw": 6.0}
    m.update(kw)
    return m


def test_fresh_breakout_with_legal_buying_is_green():
    r = E.score_row(base(), {"score": 85, "f5": 1200, "t5": 300})
    assert r["entry"] >= 85 and r["light"] == "green" and r["stage"] == "起漲"
    assert r["entry_parts"] == {"pos": 30, "brk": 24.5, "base": 15, "chip": 25.5}
    assert len(r["reasons"]) == 4 and "外資 5 日 +1,200 張" in r["reasons"][3]


def test_extended_rally_scores_low_on_position():
    r = E.score_row(base(bias=26, ret20=35, pre_bw=32), {"score": 60, "f5": 0, "t5": 0})
    assert r["stage"] == "高檔" and r["entry_parts"]["pos"] == 0 and r["entry_parts"]["base"] == 0
    assert r["light"] == "red" and "追高" in r["reasons"][0]


def test_volume_without_price_is_not_a_breakout():
    r = E.score_row(base(chg=0, amp=1.0, vr=15.7), None)
    assert r["entry_parts"]["brk"] == 0 and "換手" in r["reasons"][1]
    assert r["entry_parts"]["chip"] == 15 and r["alpha"] is None


def test_selling_pressure_caps_breakout():
    r = E.score_row(base(power=-11000, close_pos=0.2), {"score": 50})
    assert r["entry_parts"]["brk"] <= 8 and "賣壓" in r["reasons"][1]


def test_stage_boundaries():
    assert E.stage(-5, 0) == "季線下"
    assert E.stage(5, 5) == "起漲"
    assert E.stage(12, 5) == "中段" and E.stage(5, 20) == "中段"
    assert E.stage(21, 5) == "高檔" and E.stage(5, 31) == "高檔"
