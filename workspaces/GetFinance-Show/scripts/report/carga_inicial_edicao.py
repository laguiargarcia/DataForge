# carga_inicial_edicao.py — semeia o banco das tabelas editáveis (df-cns etapa 1). Uma vez por
# workspace. Grava cada tabela só se estiver vazia: rodar de novo depois de editar não muda nada.
# Uso: python scripts/report/carga_inicial_edicao.py --dicionario <csv> --mapeamento <csv>
#   (caminhos relativos partem da raiz do workspace)
#   dicionario: colunas subcategoria,categoria,Tipo · mapeamento: colunas padrao,subcategoria
import argparse
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent))
from edicao import abrir, carga_inicial

parser = argparse.ArgumentParser()
parser.add_argument("--dicionario", required=True)
parser.add_argument("--mapeamento", required=True)
args = parser.parse_args()



def _caminho(arquivo: str) -> Path:
    # Relativo à raiz do workspace (a task do job roda com o diretório de quem chamou o dataforge).
    p = Path(arquivo)
    return p if p.is_absolute() else _workspace_root() / p


dicionario = pl.read_csv(_caminho(args.dicionario), encoding="utf8", infer_schema_length=0)
mapeamento = pl.read_csv(_caminho(args.mapeamento), encoding="utf8", infer_schema_length=0)

conn = abrir(_connection_root("edicao"))
n_dic, n_map = carga_inicial(conn, dicionario, mapeamento)
conn.close()


def _msg(n):
    return f"{n} inseridas" if n else "já tinha conteúdo, nada alterado"


print(f"categorias: {_msg(n_dic)} | mapeamento: {_msg(n_map)}")
