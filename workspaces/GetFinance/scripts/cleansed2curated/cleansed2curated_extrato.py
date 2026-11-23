# cleansed2curated_extrato.py — task `extrato` (df-z4fh.5): os extratos do banco em PDF deixados na pasta
# data/extratos/ (conexão `extratos`) viram curated.extrato (um lançamento por linha), curated.extrato_saldos
# (um saldo do dia por linha, com a conferência: saldo anterior + lançamentos - saldo do dia) e
# curated.extrato_arquivos (um PDF lido por linha, com as linhas da lista que ele ignorou). Flat, top-to-bottom.
# Lê os PDFs, cleansed.accounts e curated.dim_account; só grava essas três tabelas (inteiras): não mexe em
# lançamento do Pluggy nem no relatório, e não emite achado suspicious. A leitura está em report/extrato.py.
# - Pasta vazia ou ausente: grava as tabelas vazias.
# - PDF na pasta e a biblioteca pypdf não instalada: avisa, não mexe nas tabelas (as que já existem ficam como
#   estão) e não falha (finance_daily segue).
# - Um PDF que não deu para ler (qualquer erro: cifrado, quebrado, outro layout, data impossível) ou cuja
#   agência e conta não casam com exatamente uma conta BANK de cleansed.accounts fica de fora; os bons são
#   gravados, e no fim a task falha nomeando cada arquivo ruim e o motivo.
# - Dois arquivos com saldos diferentes para o mesmo dia: fica o saldo do arquivo dos lançamentos do dia, o dia
#   sai com divergente verdadeiro em curated.extrato_saldos, a task avisa e não falha por isso.
# - No log, um arquivo é nomeado pela posição na pasta (na ordem do nome) e pelo período, nunca pelo nome: um
#   nome pode trazer o número da conta, e o log pode ser compartilhado. O nome fica em curated.extrato_arquivos.
import argparse
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from extrato import LANCAMENTOS, SALDOS, contas_do_extrato, juntar, ler

try:
    from pypdf import PdfReader
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
          "Python: nenhum foi lido e nenhuma tabela foi mexida (é dependência do DataForge: uv sync, ou o deploy "
          "na prod)")
    sys.exit(0)
contas = read_delta_table("cleansed", "accounts") if arquivos else None

origem = {"arquivo": pl.String, "emitido_em": pl.Datetime("us"), "account_id": pl.String}
lancamentos = [pl.DataFrame(schema={**origem, **LANCAMENTOS})]
saldos = [pl.DataFrame(schema={**origem, **SALDOS})]
lidos = []    # (arquivo, account_id, emitido_em, lançamentos, saldos, linhas ignoradas) de cada PDF bom
log = []      # uma linha por PDF bom, sem o nome dele
ruins = []    # (posição, motivo)
for posicao, arquivo in enumerate(arquivos, start=1):
    nome = f"arquivo {posicao} de {len(arquivos)}"
    try:
        lido = ler([pagina.extract_text() for pagina in PdfReader(arquivo).pages])
    except Exception as erro:   # noqa: BLE001 -- any error reading one PDF leaves only that file out, named
        motivo = f"{type(erro).__name__}: {erro}".replace(str(arquivo), "<arquivo>").replace(arquivo.name, "<arquivo>")
        ruins.append((nome, f"não deu para ler: {motivo}"))
        continue
    ids = contas_do_extrato(lido, contas)
    if len(ids) != 1:
        ruins.append((nome, f"a agência e a conta do cabeçalho casam com {len(ids)} contas BANK de "
                            "cleansed.accounts (precisa ser 1)"))
        continue
    marca = {"arquivo": pl.lit(arquivo.name), "emitido_em": pl.lit(lido.emitido_em), "account_id": pl.lit(ids[0])}
    lancamentos.append(lido.lancamentos.with_columns(**marca))
    saldos.append(lido.saldos.with_columns(**marca))
    lidos.append((arquivo.name, ids[0], lido.emitido_em, lido.lancamentos.height, lido.saldos.height, lido.ignoradas))
    dias_do_arquivo = pl.concat([lido.lancamentos["dia"], lido.saldos["dia"]])
    log.append(f"extrato: {nome} ({dias_do_arquivo.min()} a {dias_do_arquivo.max()}): {lido.lancamentos.height} "
               f"lançamentos, {lido.saldos.height} saldos, {lido.ignoradas} linha(s) ignorada(s)")

extrato, dias = juntar(pl.concat(lancamentos, how="diagonal_relaxed"), pl.concat(saldos, how="diagonal_relaxed"))
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

# Contagens, posições de arquivo e dias só: nunca um valor, uma descrição, uma conta ou o nome de um arquivo,
# porque o log pode ser compartilhado. O primeiro saldo de cada conta não tem anterior (fecha nulo) e não entra
# na conta.
for linha in log:
    print(linha)
divergentes = dias.filter(pl.col("divergente"))["dia"].to_list()
if divergentes:
    print(f"extrato: AVISO: {len(divergentes)} dia(s) com saldos diferentes em arquivos diferentes (fica o saldo "
          "do arquivo dos lançamentos do dia; divergente em curated.extrato_saldos):"
          + "".join(f" {d}" for d in divergentes))
nao_fecham = dias.filter(~pl.col("fecha"))["dia"].to_list()
print(f"extrato: {len(lidos)} arquivo(s) lido(s), {extrato.height} lançamentos, {dias.height} saldos do dia, "
      f"{len(nao_fecham)} dia(s) que não fecham" + "".join(f" {d}" for d in nao_fecham))
if ruins:
    raise SystemExit(f"extrato: {len(ruins)} arquivo(s) ruim(ns) ficaram de fora: "
                     + "; ".join(f"{nome}: {motivo}" for nome, motivo in ruins))
