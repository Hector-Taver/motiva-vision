# Motiva Vision

Protótipo acadêmico (CP3, turma 2CCR) que detecta acidentes rodoviários em vídeos de câmeras fixas, envia o evento por MQTT a uma central operacional e aciona a sinalização de um ESP32 (simulado no Wokwi). A decisão final é sempre do operador humano.

**Fluxo:** vídeo → visão computacional (Python) → MQTT → central (Flask) → operador confirma/rejeita → ESP32 (LED + buzzer) → confirmação de volta à central.

- Projeto Wokwi (ESP32): https://wokwi.com/projects/477909194872625153
- Categorias de acidente: traseira, lateral e capotamento (mais SEM_ACIDENTE e INDETERMINADO).

## Requisitos

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Conta gratuita no Wokwi (para abrir o ESP32 simulado)
- Internet (broker MQTT público e download do modelo YOLO na primeira execução)

## Instalação

```bash
git clone https://github.com/Hector-Taver/motiva-vision.git
cd motiva-vision
uv sync
cp .env.example .env
```

As versões das dependências estão em `pyproject.toml` e `uv.lock`. O arquivo `.env` já traz o broker público (`broker.hivemq.com:1883`) e o RM usado nos tópicos (`562881`). Nenhuma credencial é necessária.

## Vídeos de teste

Os vídeos não estão no repositório (licença e tamanho). As fontes de cada um estão em `videos/videos.csv`. Baixe os vídeos e coloque-os em:

```
videos/dev/    (v01.mp4 ... v06.mp4)
videos/eval/   (v01.mp4 ... v06.mp4)
```

## Como executar

Rode os comandos na raiz do projeto, nesta ordem.

**1. Central (terminal A)**

```bash
uv run python central/app.py
```

Abra http://127.0.0.1:5050. A página deve mostrar o broker como conectado.

**2. ESP32 (Wokwi)**

Abra o link do projeto Wokwi em **uma única aba** e clique em Start. O LED fica verde e a central mostra o ESP32 online. O código está em `esp32/` (`sketch.ino`, `diagram.json`, `libraries.txt`).

**3. Visão computacional (terminal B)**

```bash
uv run python vision/detector.py --video videos/dev/v02.mp4
```

Uma janela mostra os veículos detectados (tecla `q` fecha). Quando um acidente é detectado, o evento é impresso, o quadro de evidência é salvo em `evidence/frames/` e o evento é publicado por MQTT.

**4. Na central**

- O evento aparece como SUSPEITA e o LED do ESP32 pisca em amarelo.
- **CONFIRMAR**: LED vermelho fixo e buzzer. **ENCERRAR**: volta ao verde.
- **REJEITAR**: volta ao verde.
- Cada comando fica verde quando o ESP32 confirma a execução.
- O histórico pode ser exportado em CSV ou JSON pelos links no topo da página.

## Testes

| O que | Comando |
|---|---|
| Evento falso, sem vídeo | `uv run python tools/fake_event.py --tipo TRASEIRA --conf 0.8 --parada` |
| Evento duplicado | rodar duas vezes `uv run python tools/fake_event.py --id EVT-TEST-dup` (só um registro aparece) |
| Avaliação em lote (dev) | `uv run python tests/evaluate.py --split dev` |
| Avaliação em lote (eval) | `uv run python tests/evaluate.py --split eval` |
| Queda do broker | no monitor serial do Wokwi, digitar `x` e Enter (simula a queda); digitar `x` de novo restabelece |

Os resultados ficam em `tests/results_dev.csv` e `tests/results_eval.csv`. Adicione `--publish` ao comando de avaliação para também enviar os eventos por MQTT.

## Tópicos MQTT

| Tópico | Sentido | Conteúdo |
|---|---|---|
| `motiva/562881/eventos` | visão → central | evento em JSON |
| `motiva/562881/comandos` | central → ESP32 | SUSPEITA, CONFIRMAR, REJEITAR, ENCERRAR |
| `motiva/562881/status` | ESP32 → central | heartbeat, ack e testamento |

## Estrutura

```
vision/     detecção, rastreamento, regras e publicação MQTT
central/    dashboard Flask + banco SQLite
esp32/      sketch, diagrama e bibliotecas do Wokwi
tools/      gerador de eventos falsos
tests/      avaliação em lote e resultados
videos/     índice dos vídeos de teste (videos.csv)
evidence/   quadros de evidência dos eventos
```

## Aviso

Simulação acadêmica. O broker é público: use somente dados fictícios. As recomendações operacionais seguem regras fictícias e não constituem protocolo médico.
