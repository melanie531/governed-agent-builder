import pytest
from backend.store import Store
from scripts.opus_capture_ticket import consume_capture
from tests.test_capture_ticket import reserve,envelope

@pytest.mark.parametrize('now',[float('nan'),float('inf'),float('-inf'),True])
def test_invalid_clock_rejected(tmp_path,now):
    s=Store(str(tmp_path/'db'));reserve(s)
    with pytest.raises(ValueError):consume_capture(s,'one',role='synthetic-role',workspace='synthetic-project',request_digest='a'*64,now=now)

def test_tiny_excess_cannot_round_down(tmp_path):
    s=Store(str(tmp_path/'db'));costs=envelope()
    costs['model_input']['usd']='0.010000000000000000000000000001'
    with pytest.raises(ValueError):reserve(s,costs=costs)
