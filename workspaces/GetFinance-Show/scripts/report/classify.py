# classify.py — task `classify` (df-cns etapa 1): aplica o roteamento de categorias do Resumo v3 a
# cada transação viva, com o dicionário e o mapeamento do banco de edição. Flat, top-to-bottom.
# Lê curated.fact_transaction + data/edicao.db; grava curated.fact_transaction_classified (inteira).
import argparse
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent))
from classificacao import SUB_SEM_CATEGORIA, classificar
from edicao import abrir, ler_tabelas

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # curated
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

conn = abrir(_connection_root("edicao"))
dicionario, mapeamento = ler_tabelas(conn)
conn.close()

fact = read_delta_table(args.source, "fact_transaction")
classificada = classificar(fact, dicionario, mapeamento)
write_delta_overwrite(args.target, "fact_transaction_classified", classificada)

sem_categoria = classificada.filter(pl.col("subcategoria") == SUB_SEM_CATEGORIA).height
print(f"fact_transaction_classified: {classificada.height} linhas ({sem_categoria} sem categoria) "
      f"| dicionário {dicionario.height} | regras {mapeamento.height}")
