import logo from '../../identidade/logo-ninho.png'

// O logo do ninho no canto do cabeçalho de cada tela: o desenho com a palavra, como manda
// ninho/identidade/README.md (nada de recolorir, esticar ou separar a palavra).
export const Logo = () => <img src={logo} alt="ninho" width={64} height={56} className="shrink-0 object-contain" />
