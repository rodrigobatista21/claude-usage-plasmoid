# Claude Usage para KDE Plasma

Um widget nativo do Plasma 6 que mostra quanto dos limites da sua assinatura do Claude Code você já usou: a janela de 5 horas, a janela de 7 dias, a janela semanal por modelo (Fable) e quanto o bloco atual de 5 horas está custando. Ele nunca consulta a Anthropic em loop e nunca mexe no seu refresh token.

[Read in English](README.md)

<p align="center">
  <img src="docs/screenshot-full.png" alt="Popup completo: barras de sessão, semana e Fable, custo do bloco atual" width="380">
  &nbsp;&nbsp;
  <img src="docs/screenshot-panel.png" alt="Anel compacto no painel" width="200">
</p>

## Por que existe

A maioria dos widgets de uso do Claude consulta o endpoint não documentado `api.anthropic.com/api/oauth/usage` a cada minuto. Esse endpoint limita **por conta**, o bloqueio pode durar horas, e algumas ferramentas então recorrem a `claude -p /cost`, que bate no mesmo endpoint e deixa um arquivo de sessão vazio a cada execução. Eu tinha 242 desses numa única pasta de projeto antes de escrever isto.

Desde o Claude Code 2.1.80, a status line já recebe `rate_limits` no stdin a cada atualização. Este projeto transforma isso na fonte principal e deixa a API como reserva rara.

## Como funciona

```
Claude Code ──stdin da statusLine──▶ claude-statusline-wrapper.sh ──▶ sua status line (inalterada)
                                          │
                                          └─▶ statusline_capture.py ──┐
                                                                       ├─▶ ~/.cache/claude-usage/usage.json
timer do systemd (5 min) ──▶ collector.py ────────────────────────────┘             ▲
   ├─ API oauth/usage só se o cache estiver velho (≥10 min, com jitter,             │ lido a cada 30 s
   │  respeitando Retry-After e o lock do ccstatusline)                             │
   └─ ccusage blocks --active só enquanto há sessão ativa                           │
                                                             plasmoid (QML) ────────┘
```

- **Wrapper da status line**: um shell script pequeno registrado como comando `statusLine` do Claude Code. Grava `rate_limits` no cache e repassa o mesmo JSON para a status line que você já usa (ccstatusline, script próprio, qualquer uma).
- **Coletor**: um script Python oneshot rodado por um timer do systemd do usuário. Chama a API de uso no máximo a cada 10 minutos, só quando o cache está velho, recua em caso de 429 (de 5 min a 1 h, respeitando `Retry-After`) e nunca renova o token OAuth. Também roda o [ccusage](https://github.com/ryoppippi/ccusage) para o bloco atual, só enquanto há sessão ativa.
- **Plasmoid**: lê o arquivo de cache e nada mais. Anel compacto no painel, popup completo com barras, contagem regressiva, previsão de esgotamento e tokens e custo do bloco atual. As cores seguem o esquema de cores do Plasma.
- **Notificações**: avisos de área de trabalho em 90% e 100% da janela de sessão e em 90% da semanal, uma vez por janela, mais um quando uma janela que bateu 100% reseta. Saem do coletor, então funcionam mesmo sem o widget no painel.

## Requisitos

- KDE Plasma 6 (testado no 6.6, Wayland), `kpackagetool6`, `systemd --user`.
- Python 3.9 ou mais novo (só biblioteca padrão).
- Claude Code 2.1.80 ou mais novo com assinatura Pro ou Max (`rate_limits` só existe nelas).
- Opcional: [ccusage](https://github.com/ryoppippi/ccusage) para a seção "bloco atual"; `notify-send` para as notificações.

## Instalação

```bash
git clone https://github.com/rodrigobatista21/claude-usage-plasmoid.git
cd claude-usage-plasmoid
./install.sh
```

O instalador:

1. copia o coletor para `~/.local/share/claude-usage/` e o wrapper para `~/.local/bin/`;
2. instala e inicia a unit `claude-usage-collector.timer`;
3. aponta `statusLine.command` em `~/.claude/settings.json` para o wrapper, guardando a status line anterior para continuar rodando (um backup do `settings.json` fica ao lado dele);
4. instala o plasmoid com `kpackagetool6`.

Depois clique com o botão direito num painel ou na área de trabalho, escolha **Adicionar widgets...** e procure **Claude Usage**. Abra uma sessão do Claude Code e os números aparecem em segundos.

Para atualizar, `git pull`, rode `./install.sh` de novo e depois `systemctl --user restart plasma-plasmashell.service` para o Plasma recarregar o QML.

## Configuração

Botão direito no widget > **Configurar**:

| Opção | Padrão | O que faz |
|---|---|---|
| Métrica no painel | Sessão (5 h) | Qual janela o anel do painel mostra |
| Mostrar porcentagem | ligado | Número ao lado do anel |
| Bloco atual | ligado | Tokens, custo, consumo e projeção do ccusage |
| Reler o cache a cada | 30 s | Frequência com que o widget lê o cache |
| Considerar velho após | 15 min | Marca o dado como velho durante uma sessão ativa |
| Arquivo de cache | `$HOME/.cache/claude-usage/usage.json` | Onde o coletor grava |

Os tempos do coletor ficam no topo de `collector/collector.py`.

## Conferindo se está funcionando

```bash
# decisões do coletor ao vivo (uma linha cada: skip / api: 200 / api: 429 -> backoff / ccusage: ok)
journalctl --user -t claude-usage-collector -f

# o que o widget enxerga
jq . ~/.cache/claude-usage/usage.json

# forçar uma atualização (o mesmo que o botão do widget faz)
systemctl --user start claude-usage-refresh.service

# rodar o widget avulso (precisa do plasma-sdk)
plasmoidviewer -a org.rodrigom.claudeusage -f planar
```

## Notas de segurança

- O coletor lê o access token de `~/.claude/.credentials.json` e só o usa em `GET /api/oauth/usage`. Nunca chama o endpoint de renovação: refresh tokens são de uso único, e um segundo cliente disputando com o Claude Code pode derrubar seu login.
- O plasmoid não tem acesso à rede. Ele roda `cat` no arquivo de cache pelo data engine de executáveis do Plasma.
- Nada é enviado para nenhum outro lugar.

## Desinstalação

```bash
./uninstall.sh
```

Restaura a status line anterior, desativa o timer, remove o plasmoid e apaga o cache. Remova o widget do painel se ele ainda estiver lá.

## Limitações conhecidas

- Os textos do widget estão em português do Brasil. Traduções são bem-vindas.
- `rate_limits` só aparece depois da primeira resposta da API numa sessão do Claude Code, então uma sessão nova leva um ou dois prompts até o widget atualizar. O coletor cobre o intervalo com a API quando precisa.
- As janelas por modelo (Fable hoje) vêm só da API, então atualizam a cada 10 minutos, não a cada poucos segundos.

## Créditos

- [ccusage](https://github.com/ryoppippi/ccusage) pela contabilidade local dos blocos.
- [ccstatusline](https://github.com/sirmalloc/ccstatusline) por mostrar que o payload da status line bastava.

Licença MIT.
