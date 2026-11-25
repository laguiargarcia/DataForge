import logo from '../../identidade/logo-ninho.svg'

// O logo do ninho no canto do cabeçalho de cada tela: o desenho com a palavra, como manda
// ninho/identidade/README.md (nada de recolorir, esticar ou separar a palavra).
// `largura` em px; a altura segue a proporção do desenho (58 por 60).
export const Logo = ({ largura = 58 }: Readonly<{ largura?: number }>) => (
  <img src={logo} alt="ninho" width={largura} height={Math.round(largura * 60 / 58)} className="shrink-0 object-contain" />
)
