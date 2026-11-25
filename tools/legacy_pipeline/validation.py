"""Native Excel preservation checks. Formula caches may recalculate normally."""
import zipfile


def cells(book):
    result={}
    for sheet in book.sheets:
        used=sheet.api.UsedRange;raw=used.Formula;r0=int(used.Row)-1;c0=int(used.Column)-1
        matrix=raw if isinstance(raw,(tuple,list)) else ((raw,),)
        if matrix and not isinstance(matrix[0],(tuple,list)):
            matrix=(matrix,)
        result[sheet.name]={(r0+r,c0+c):value for r,line in enumerate(matrix)
                            for c,value in enumerate(line) if value is not None and value!=''}
    return result


def verify_cells(before,after,allowed):
    if set(before)!=set(after):
        raise ValueError('Изменился набор листов; публикация отменена')
    for sheet in before:
        for cell in before[sheet].keys()|after[sheet].keys():
            if (sheet,*cell) not in allowed and before[sheet].get(cell)!=after[sheet].get(cell):
                raise ValueError(f'Незапланированное изменение {sheet}, строка {cell[0]+1}, колонка {cell[1]+1}')


def macros(path):
    with zipfile.ZipFile(path) as z:
        return {name:z.read(name) for name in z.namelist() if name.casefold().endswith('vbaproject.bin')}
