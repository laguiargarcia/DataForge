import logo from '../../identidade/logo-ninho.svg'

// O logo do ninho no canto do cabeçalho de cada tela: o desenho com a palavra, como manda
// ninho/identidade/README.md (nada de recolorir, esticar ou separar a palavra).
export const Logo = () => <img src={logo} alt="ninho" width={58} height={60} className="shrink-0 object-contain" />
