# backup_dados.py — task `backup` (DataForge df-sgyf): copia o que não tem reposição para um zip
# datado fora do repo. Só data/raw (o Pluggy devolve pouco histórico), data/edicao.db (categorias e
# regras editadas) e data/extratos (os PDFs do banco, df-z4fh.5) não se refazem; cleansed se reconstrói do
# raw e curated é recriado a cada run. A pasta de extratos pode não existir ainda: o zip vai sem ela.
# Flat, top-to-bottom. --destino é relativo à raiz do workspace (vale igual no WSL e no Windows).
import argparse
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))

parser = argparse.ArgumentParser()
parser.add_argument("--destino", required=True)        # pasta dos zips, relativa ao workspace
parser.add_argument("--manter", type=int, required=True)  # quantos zips mais novos ficam
args = parser.parse_args()
if args.manter < 1:
    raise SystemExit("--manter precisa ser pelo menos 1")

raw = _connection_root("raw")
edicao = _connection_root("edicao")
extratos = _connection_root("extratos")
if not raw.is_dir():
    raise SystemExit(f"raw não encontrado: {raw}")
if not edicao.is_file():
    raise SystemExit(f"edicao.db não encontrado: {edicao}")   # sqlite3.connect criaria um vazio

destino = (_workspace_root() / args.destino).resolve()
destino.mkdir(parents=True, exist_ok=True)
nome = f"getfinance-{datetime.now(timezone.utc):%Y-%m-%dT%H%M%S-%fZ}.zip"   # UTC: ordena igual em qualquer máquina
parcial = destino / f"{nome}.parcial"

with tempfile.TemporaryDirectory() as tmp:
    # Cópia consistente mesmo com o app escrevendo: a API de backup do sqlite, não um copy do arquivo.
    copia_edicao = Path(tmp) / "edicao.db"
    origem = sqlite3.connect(f"file:{edicao.as_posix()}?mode=ro", uri=True)
    copia = sqlite3.connect(copia_edicao)
    origem.backup(copia)
    copia.close()
    origem.close()

    # Sem o temporário de uma escrita de partição que morreu no meio (df-ahua.10.5): não é dado.
    arquivos_raw = sorted(f for f in raw.rglob("*") if f.is_file() and f.name != RAW_PARTITION_TEMP)
    arquivos_extratos = sorted(f for f in extratos.rglob("*") if f.is_file()) if extratos.is_dir() else []
    with zipfile.ZipFile(parcial, "w", zipfile.ZIP_DEFLATED) as z:
        for f in arquivos_raw:
            z.write(f, (Path("raw") / f.relative_to(raw)).as_posix())
        z.write(copia_edicao, "edicao.db")
        for f in arquivos_extratos:
            z.write(f, (Path("extratos") / f.relative_to(extratos)).as_posix())

with zipfile.ZipFile(parcial) as z:
    corrompido = z.testzip()
if corrompido is not None:
    parcial.unlink()
    raise SystemExit(f"zip corrompido em {corrompido}; nada foi gravado")
parcial.replace(destino / nome)   # o zip só aparece com o nome final quando está completo

antigos = sorted(destino.glob("getfinance-*.zip"))[:-args.manter]
for velho in antigos:
    velho.unlink()

tamanho_mb = (destino / nome).stat().st_size / 1_000_000
print(f"backup: {destino / nome} ({len(arquivos_raw)} arquivos de raw + edicao.db + "
      f"{len(arquivos_extratos)} extrato(s), {tamanho_mb:.1f} MB)"
      f" | {len(antigos)} antigo(s) apagado(s), ficam {args.manter}")
