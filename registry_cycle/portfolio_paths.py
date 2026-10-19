"""Bounded discovery of secondary personal books next to an active Soft book."""
from pathlib import Path

SECONDARY_CONTAINERS = frozenset({'софт', 'софты'})


def name_key(value: str) -> str:
    return ' '.join(value.strip().casefold().split())


def secondary_folders(root: Path, container: Path) -> list[Path]:
    """Only root/city/СОФТЫ/person with a current root/Soft/person/person.xlsb.

    Folder labels describe geometry. Callers still verify the workbook columns
    before importing or enriching it. No archive or arbitrary subtree is walked.
    """
    if container.name.casefold() not in SECONDARY_CONTAINERS:
        return []
    if container.parent.parent != root:
        return []
    soft_roots = [p for p in root.iterdir() if p.is_dir() and p.name.casefold() == 'soft']
    result = []
    for person in container.iterdir():
        if not person.is_dir() or person.name.startswith(('!', '_')) or person.name.casefold().startswith('архив'):
            continue
        for soft_root in soft_roots:
            matching = [p for p in soft_root.iterdir() if p.is_dir() and name_key(p.name) == name_key(person.name)]
            active = [p for folder in matching for p in folder.iterdir()
                      if p.is_file() and p.suffix.casefold() == '.xlsb'
                      and name_key(p.stem) == name_key(person.name)]
            if len(active) == 1:
                result.append(person)
                break
    return sorted(result, key=lambda p: name_key(p.name))
