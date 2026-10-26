"""Reproduce the supplied read-only starter installation; never executes package code."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

EXPECTED_STARTER_SHA256="9c018b5bda2fd9621344ace1b9b4267f8548fd786945fcd8cfcd6e405ea80704"
MAX_FILES=10000
MAX_EXPANDED=2_000_000_000
REPO=Path(__file__).resolve().parents[2]

def unpack_bytes(raw, destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist()
        if len(infos)>MAX_FILES or sum(i.file_size for i in infos)>MAX_EXPANDED:
            raise ValueError("Archive size/file budget exceeded")
        names=set()
        for info in infos:
            name=info.filename
            p=PurePosixPath(name)
            if (not name or "\\" in name or ":" in name or p.is_absolute()
                    or ".." in p.parts or name in names or ((info.external_attr>>16)&0o170000)==0o120000):
                raise ValueError("Unsafe/duplicate archive entry")
            names.add(name)
            target=destination.joinpath(*p.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("Archive path escape")
            if info.is_dir():
                continue
            contents=z.read(info)
            if target.exists():
                if target.is_symlink() or target.read_bytes()!=contents:
                    raise ValueError("Existing package content differs: "+name)
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("xb") as f:
                f.write(contents)

def install(archive,destination):
    raw=Path(archive).read_bytes()
    h=hashlib.sha256(raw).hexdigest()
    if h!=EXPECTED_STARTER_SHA256:
        raise ValueError("Starter differs from owner-supplied 26 October 2026 bundle")
    destination=Path(destination)
    unpack_bytes(raw,destination)
    manifest=(destination/"SHA256_MANIFEST.txt").read_text(encoding="utf-8")
    verified=[]
    for line in manifest.splitlines():
        m=re.fullmatch(r"([a-f0-9]{64})\s+(.+)",line.strip())
        if not m:
            raise ValueError("Invalid starter manifest")
        relative=PurePosixPath(m[2])
        if relative.is_absolute() or ".." in relative.parts or "\\" in m[2] or ":" in m[2]:
            raise ValueError("Unsafe manifest entry")
        data=(destination/relative).read_bytes()
        if hashlib.sha256(data).hexdigest()!=m[1]:
            raise ValueError("Starter entry hash mismatch")
        verified.append({"path":m[2],"sha256":m[1],"bytes":len(data)})
    unpack_bytes((destination/"01_ENGINE/PuntProof_Adaptive_V3_Runtime.zip").read_bytes(),destination/"engine")
    unpack_bytes((destination/"02_BACKTEST/PuntProof_2026_F_vs_V3_Audit_and_Monitor.zip").read_bytes(),destination/"comparison")
    return {"starter_sha256":h,"verified_members":verified,"destination":str(destination),
            "engine_executed":False,"joblib_loaded":False}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--destination",type=Path,default=REPO/"scratch/research-lab-starter")
    args=p.parse_args()
    print(json.dumps(install(args.archive,args.destination),indent=2))
