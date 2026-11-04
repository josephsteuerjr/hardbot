"""Persisted command/baseline hashes must remain compatible across upgrades."""
import hashlib
import json
import pytest
from crm.contract import digest


@pytest.mark.parametrize('value',[None,{'z':0,'a':['Привет',None,'',False,'=literal',1.25]},
    {'rows':[{'contract_uuid':str(i),'values':{'note':'Сумма € \n \\"','amount':'0.00'}} for i in range(3)]},
    {'rows':[{'id':i,'fields':{'zero':'0.00','null':None,'text':'Текст'*100}} for i in range(1000)]}])
def test_streaming_hash_matches_persisted_canonical_wire_bytes(value):
    # This is the previous released wire representation, independent of the
    # new incremental encoder. Old queued requests and manifests use it.
    previous=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    assert digest(value)==hashlib.sha256(previous).hexdigest()
