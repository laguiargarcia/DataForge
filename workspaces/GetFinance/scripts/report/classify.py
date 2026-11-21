# classify.py — task `classify` (df-cns etapa 1): aplica o roteamento de categorias do Resumo v3 a
# cada transação viva, com o dicionário, o mapeamento e as correções do banco de edição. Flat,
# top-to-bottom. Lê curated.fact_transaction + data/edicao.db; grava curated.fact_transaction_classified
# (inteira) e curated.edicao_aplicada (uma linha: a versão do banco que esta classificação usou, que
# a tela de edição compara com a versão atual para avisar edição ainda não recalculada; df-cns.8).
import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent))
from classificacao import SUB_SEM_CATEGORIA, classificar
from edicao import abrir, ler_correcoes, ler_tabelas, ler_versao

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # curated
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

conn = abrir(_connection_root("edicao"))
dicionario, mapeamento = ler_tabelas(conn)
correcoes = ler_correcoes(conn)
versao = ler_versao(conn)
conn.close()

fact = read_delta_table(args.source, "fact_transaction")
classificada = classificar(fact, dicionario, mapeamento, correcoes)
# Checagens antes da escrita (ADR 0004): uma linha por transação viva (sem netting_applied), nem
# perdida nem repetida (df-ahua.22).
validate(classificada, "curated.fact_transaction_classified", FACT_TRANSACTION_CLASSIFIED_RULES)
validate(layer_totals(fact.filter(~pl.col("netting_applied").fill_null(False)), classificada, ["id"]),
         "curated.fact_transaction_classified vs fact_transaction", ROWS_CARRIED_RULES)
write_delta_overwrite(args.target, "fact_transaction_classified", classificada)
# Depois da classificação: se ela falhar, a versão aplicada fica a de antes e a tela segue avisando.
write_delta_overwrite(args.target, "edicao_aplicada", pl.DataFrame(
    {"versao": [versao], "aplicada_em": [datetime.now(UTC)]},
    schema={"versao": pl.Int64, "aplicada_em": pl.Datetime("us", "UTC")},
))

sem_categoria = classificada.filter(pl.col("subcategoria") == SUB_SEM_CATEGORIA).height
print(f"fact_transaction_classified: {classificada.height} linhas ({sem_categoria} sem categoria) "
      f"| dicionário {dicionario.height} | regras {mapeamento.height} | correções "
      f"{classificada['corrigida'].sum()} de {correcoes.height} | versão {versao}")
