"""The finance edits a person makes on a screen, kept in the app database (ninho/banco.py).

The skeleton handles one kind of edit, the correction of one transaction's category; the rest of
today's edits (categories, rules, explanations) come with the screens that need them. Every write
goes through the rules module, which validates, writes and adds 1 to the version in one transaction.
"""
from pathlib import Path

from ninho import banco
from ninho.financas import leitura, regras


class Recusa(Exception):
    """A write that was refused, with the message for the screen. ``motivo`` is the rules' own:
    "invalido", "conflito" or "ausente"."""

    def __init__(self, motivo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.motivo = motivo


def ler(workspace: Path) -> dict:
    """What a screen needs to edit: the corrections, the categories to choose from, the version of
    the edits and the version the pipeline last applied (``pendente`` when they differ)."""
    edicao = regras.edicao(workspace)
    with banco.aberto(workspace) as conn:
        versao = edicao.ler_versao(conn)
        correcoes = edicao.ler_correcoes(conn).to_dicts()
        categorias = edicao.ler_dicionario(conn).rename({"Tipo": "tipo"}).to_dicts()
    aplicada = leitura.versao_aplicada(workspace)
    return {"versao": versao, "versao_aplicada": aplicada, "pendente": versao != (aplicada or 0),
            "correcoes": correcoes, "categorias": categorias}


def _gravar(workspace: Path, funcao: str, *args: str) -> None:
    edicao = regras.edicao(workspace)
    with banco.aberto(workspace) as conn:
        try:
            getattr(edicao, funcao)(conn, *args)
        except edicao.Recusa as recusa:
            raise Recusa(recusa.motivo, str(recusa)) from recusa


def corrigir(workspace: Path, transacao_id: str, subcategoria: str) -> None:
    """Set the category of one transaction, which must be one the pipeline published."""
    if not leitura.existe(workspace, transacao_id):
        raise Recusa("ausente", "essa transação não existe")
    _gravar(workspace, "salvar_correcao", transacao_id, subcategoria)


def desfazer(workspace: Path, transacao_id: str) -> None:
    _gravar(workspace, "excluir_correcao", transacao_id)
