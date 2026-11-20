# cleansed2curated_extrato.py — task `extrato` (df-z4fh.5): os extratos do banco em PDF deixados na pasta
# data/extratos/ (conexão `extratos`) viram curated.extrato (um lançamento por linha), curated.extrato_saldos
# (um saldo do dia por linha, com a conferência: saldo anterior + lançamentos - saldo do dia) e
# curated.extrato_arquivos (um PDF lido por linha, com as linhas da lista que ele ignorou). Flat, top-to-bottom.
# Lê os PDFs, cleansed.accounts e curated.dim_account; só grava essas três tabelas (inteiras): não mexe em
# lançamento do Pluggy nem no relatório, e não emite achado suspicious. A leitura está em report/extrato.py.
# - Pasta vazia ou ausente: grava as tabelas vazias.
# - PDF na pasta e a biblioteca pypdf não instalada: avisa, grava as tabelas vazias e não falha (finance_daily
#   segue).
# - Um PDF ilegível (cifrado, quebrado), com outro layout ou data impossível, ou cuja agência e conta não casam
#   com exatamente uma conta BANK de cleansed.accounts fica de fora; os bons são gravados, e no fim a task
#   falha nomeando cada arquivo ruim e o motivo.
# - Dois arquivos com saldos diferentes para o mesmo dia: a task falha antes de gravar, nomeando os dois.
import argparse
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from extrato import LANCAMENTOS, SALDOS, SaldosDivergentes, contas_do_extrato, juntar, ler

try:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError, PyPdfError
except ImportError:
    PdfReader = None

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # extratos (a pasta dos PDFs)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

pasta = _connection_root(args.source)
arquivos = sorted(f for f in pasta.iterdir() if f.is_file() and f.suffix.lower() == ".pdf") if pasta.is_dir() else []
if arquivos and PdfReader is None:
    print(f"extrato: AVISO: {len(arquivos)} PDF(s) na pasta, mas a biblioteca pypdf não está instalada neste "
          "Python: nenhum foi lido e as tabelas ficam vazias (é dependência do DataForge: uv sync, ou o deploy na prod)")
    arquivos = []
contas = read_delta_table("cleansed", "accounts") if arquivos else None

origem = {"arquivo": pl.String, "emitido_em": pl.Datetime("us"), "account_id": pl.String}
lancamentos = [pl.DataFrame(schema={**origem, **LANCAMENTOS})]
saldos = [pl.DataFrame(schema={**origem, **SALDOS})]
lidos = []    # (arquivo, account_id, emitido_em, lançamentos, saldos, linhas ignoradas) de cada PDF bom
ruins = []    # (arquivo, motivo)
for arquivo in arquivos:
    try:
        lido = ler([pagina.extract_text() for pagina in PdfReader(arquivo).pages])
    except (ValueError, PyPdfError, DependencyError) as erro:   # LayoutDesconhecido é um ValueError
        ruins.append((arquivo.name, f"não deu para ler: {erro}"))
        continue
    ids = contas_do_extrato(lido, contas)
    if len(ids) != 1:
        ruins.append((arquivo.name, f"a agência e a conta do cabeçalho casam com {len(ids)} contas BANK de "
                                    "cleansed.accounts (precisa ser 1)"))
        continue
    marca = {"arquivo": pl.lit(arquivo.name), "emitido_em": pl.lit(lido.emitido_em), "account_id": pl.lit(ids[0])}
    lancamentos.append(lido.lancamentos.with_columns(**marca))
    saldos.append(lido.saldos.with_columns(**marca))
    lidos.append((arquivo.name, ids[0], lido.emitido_em, lido.lancamentos.height, lido.saldos.height, lido.ignoradas))

try:
    extrato, dias = juntar(pl.concat(lancamentos, how="diagonal_relaxed"),
                           pl.concat(saldos, how="diagonal_relaxed"))
except SaldosDivergentes as erro:
    raise SystemExit(f"extrato: saldos diferentes para o mesmo dia em arquivos diferentes, nada foi gravado: "
                     f"{erro}") from erro
por_arquivo = pl.DataFrame(lidos, orient="row", schema={
    "arquivo": pl.String, "account_id": pl.String, "emitido_em": pl.Datetime("us"), "n_lancamentos": pl.Int64,
    "n_saldos": pl.Int64, "linhas_ignoradas": pl.Int64})

# As tabelas são por account_uuid, como as outras do curated; dim_account leva do id do Pluggy até ele.
contas_uuid = (
    read_delta_table(args.target, "dim_account").select(pl.col("pluggy_account_id").alias("account_id"),
                                                        "account_uuid")
    if lidos else pl.DataFrame(schema={"account_id": pl.String, "account_uuid": pl.String})
)
extrato = extrato.join(contas_uuid, on="account_id", how="left").select(EXTRATO_COLUMNS)
dias = dias.join(contas_uuid, on="account_id", how="left").select(EXTRATO_SALDOS_COLUMNS)
por_arquivo = por_arquivo.join(contas_uuid, on="account_id", how="left").select(EXTRATO_ARQUIVOS_COLUMNS)

validate(extrato, "curated.extrato", EXTRATO_RULES, ["dia", "ordem"])
validate(dias, "curated.extrato_saldos", EXTRATO_SALDOS_RULES, ["dia"])
validate(por_arquivo, "curated.extrato_arquivos", EXTRATO_ARQUIVOS_RULES, ["arquivo"])
write_delta_overwrite(args.target, "extrato", extrato)
write_delta_overwrite(args.target, "extrato_saldos", dias)
write_delta_overwrite(args.target, "extrato_arquivos", por_arquivo)

# Contagens, nomes de arquivo e dias só: nunca um valor, uma descrição ou uma conta, porque o log pode ser
# compartilhado. O primeiro saldo de cada conta não tem anterior (fecha nulo) e não entra na conta.
for nome, _, _, n_lancamentos, n_saldos, ignoradas in lidos:
    print(f"extrato: {nome}: {n_lancamentos} lançamentos, {n_saldos} saldos, {ignoradas} linha(s) ignorada(s)")
nao_fecham = dias.filter(~pl.col("fecha"))["dia"].to_list()
print(f"extrato: {len(lidos)} arquivo(s) lido(s), {extrato.height} lançamentos, {dias.height} saldos do dia, "
      f"{len(nao_fecham)} dia(s) que não fecham" + "".join(f" {d}" for d in nao_fecham))
if ruins:
    raise SystemExit(f"extrato: {len(ruins)} arquivo(s) ruim(ns) ficaram de fora: "
                     + "; ".join(f"{nome}: {motivo}" for nome, motivo in ruins))
