"""Include installed distributions' license texts and metadata in binary releases."""
import importlib.metadata
import shutil
import sys
from pathlib import Path

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
for dist in importlib.metadata.distributions():
    name = dist.metadata.get("Name", "unknown")
    for item in dist.files or []:
        if any(word in str(item).lower() for word in ("license", "copying", "notice")) or str(item).endswith(".dist-info/METADATA"):
            source = Path(dist.locate_file(item))
            if source.is_file():
                destination = target / name / str(item).replace("..", "_")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
