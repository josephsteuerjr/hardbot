import struct,zipfile
from pult.core.manual_workbooks import calculation_metadata,preserve_calculation_metadata,_raw_records


def test_xlsb_calculation_record_is_restored_without_touching_any_other_record(tmp_path):
    source=tmp_path/'original.xlsb';candidate=tmp_path/'candidate.xlsb'
    def content(mode):
        props=struct.pack('<III',191029,mode,100)+struct.pack('<d',.001)+struct.pack('<IH',1,74)
        return b'\x27\x04name'+b'\x9d\x01'+bytes([len(props)])+props+b'\x27\x04tail'
    for p,mode in [(source,1),(candidate,0)]:
        with zipfile.ZipFile(p,'w') as z:z.writestr('xl/workbook.bin',content(mode));z.writestr('untouched.bin',b'preserve')
    preserve_calculation_metadata(source,candidate)
    assert calculation_metadata(source)==calculation_metadata(candidate)
    with zipfile.ZipFile(candidate) as z:
        assert z.read('xl/workbook.bin')==content(1)
        assert z.read('untouched.bin')==b'preserve'
