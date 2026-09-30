/**
 * Allowlist de parâmetros do viral-clipper.
 *
 * Esta é a ÚNICA fonte de verdade sobre o que o agente pode configurar.
 * O agente (LLM) nunca escreve uma linha de shell: ele devolve um objeto JSON
 * de parâmetros, que é validado aqui contra esta allowlist e só então vira um
 * argv. Qualquer chave fora daqui é rejeitada com erro explícito, o que fecha a
 * porta para injeção de comando e para o modelo "inventar" flags inexistentes.
 *
 * Espelhado de viralclipper/cli.py e viralclipper/config.py. Quando um flag
 * novo aparecer no CLI, ele precisa ser adicionado aqui conscientemente — a
 * lista fechada é uma decisão de segurança, não um esquecimento.
 */

export type OptionKind = 'number' | 'int' | 'float' | 'boolean' | 'string' | 'enum';

export interface ClipOption {
  /** Chave aceita no JSON que o agente envia. */
  key: string;
  /** Flag equivalente no CLI (undefined = não vira flag, é tratado no servidor). */
  flag?: string;
  kind: OptionKind;
  /** Valores aceitos quando kind === 'enum'. */
  values?: readonly string[];
  min?: number;
  max?: number;
  /** Descrição em pt-BR, exposta ao agente para ele escolher bem. */
  description: string;
  /** Grupo lógico, só para organizar o "cardápio" mostrado ao agente. */
  group: string;
  /** true = o CLI já liga por padrão; o agente só deve citar se quiser mudar. */
  defaultOn?: boolean;
  /** Valor default real no CLI, para o resumo que o usuário confirma. */
  default?: string | number | boolean;
}

/** Nomes reais dos presets, extraídos de viralclipper/caption_presets.py. */
export const CAPTION_PRESETS = [
  'karaoke', 'social', 'bold-box', 'minimal', 'neon', 'block-dark', 'mono',
  'fire', 'magenta-pop', 'cyan-pop', 'lime-hit', 'blood', 'gold-box', 'candy',
  'violet-vibe', 'ice-blue', 'sunset', 'bubble', 'ultra-impact', 'slim',
  'cobalt', 'pop-box', 'roboto-bold', 'inter-bold', 'poppins-bold',
  'montserrat-bold', 'dm-sans', 'cabin-bold', 'verdana-bold', 'trebuchet-bold',
  'tahoma-bold', 'calibri-bold', 'franklin-bold', 'segoe-black',
  'helvetica-classic', 'merriweather-black', 'arvo-bold',
] as const;

/** Descrição de cada preset, para o agente escolher por intenção. */
export const CAPTION_PRESET_DESCRIPTIONS: Record<string, string> = {
  karaoke: 'Karaokê clássico — branco com destaque amarelo por palavra',
  social: 'Padrão TikTok/Reels — pop amarelo de 2 palavras na zona segura',
  'bold-box': 'Texto branco sobre caixa escura — legível em qualquer fundo',
  minimal: 'Discreto — caixa de frase, contorno fino, sem caixa',
  neon: 'Destaque verde neon em fundo escuro — alto contraste',
  'block-dark': 'Bloco escuro opaco — separação máxima do vídeo',
  mono: 'Monoespaçada — canais de tecnologia e código',
  fire: 'Destaque laranja-fogo — energia e urgência',
  'magenta-pop': 'Destaque magenta vivo — pop e irreverente',
  'cyan-pop': 'Destaque ciano elétrico — moderno e frio',
  'lime-hit': 'Destaque lima ácido — juvenil, alto contraste',
  blood: 'Destaque vermelho sangue, contorno grosso — drama e choque',
  'gold-box': 'Destaque dourado sobre caixa escura — autoridade e premium',
  candy: 'Caixa branca, texto escuro, destaque rosa — doce e claro',
  'violet-vibe': 'Destaque violeta com sombra funda — criativo e noturno',
  'ice-blue': 'Destaque azul-gelo em caixa escura fina — limpo e técnico',
  sunset: 'Destaque laranja-pôr-do-sol — quente sem gritar',
  bubble: 'Caixa azul-noite, destaque amarelo — conversa e podcast',
  'ultra-impact': 'Impact gigante, contorno grosso — o máximo de impacto',
  slim: 'Narrow com destaque ciano — compacto e informativo',
  cobalt: 'Caixa escura com destaque ciano — corporativo afiado',
  'pop-box': 'Caixa amarela, texto escuro, destaque vermelho — chamativo',
  'roboto-bold': 'Roboto — padrão dos shorts e do YouTube, destaque ciano',
  'inter-bold': 'Inter — legibilidade máxima em tela pequena, destaque lima',
  'poppins-bold': 'Poppins — geométrica e redonda, destaque magenta',
  'montserrat-bold': 'Montserrat — estilo TikTok, destaque dourado',
  'dm-sans': 'DM Sans — minimalismo suíço, destaque laranja',
  'cabin-bold': 'Cabin — aberta e amigável, destaque ciano claro',
  'verdana-bold': 'Verdana — x-height gigante, destaque amarelo',
  'trebuchet-bold': 'Trebuchet MS — humanista e limpa, destaque vermelho',
  'tahoma-bold': 'Tahoma — estreita e legível, destaque ciano',
  'calibri-bold': 'Calibri — clara e moderna, destaque lima',
  'franklin-bold': 'Franklin Gothic — condensada clássica, destaque fogo',
  'segoe-black': 'Segoe UI — moderna nativa, destaque violeta',
  'helvetica-classic': 'Helvetica — o amarelo clássico do cinema',
  'merriweather-black': 'Merriweather — serifada editorial para ritmo lento',
  'arvo-bold': 'Arvo — serifada de telão para entrevistas',
};

export const CAPTION_TEMPLATES = ['full-frame', 'split-card'] as const;

/**
 * Campos aceitos. Deliberadamente AUSENTES: output_dir, work_dir, ffmpeg,
 * ffprobe, extra_ytdlp_args, cookies_from_browser, cache_dir, transcript_file.
 * São caminhos, binários ou vetores de execução arbitrária — o chat não decide
 * nada disso; o servidor fixa.
 */
export const CLIP_OPTIONS: readonly ClipOption[] = [
  // ---- seleção -----------------------------------------------------------
  {
    key: 'count', flag: '-n', kind: 'int', min: 1, max: 50, default: 5, group: 'seleção',
    description: 'Quantidade de cortes a gerar.',
  },
  {
    key: 'min_duration', flag: '--min', kind: 'float', min: 5, max: 600, default: 30, group: 'seleção',
    description: 'Duração mínima de cada corte, em segundos.',
  },
  {
    key: 'max_duration', flag: '--max', kind: 'float', min: 5, max: 600, default: 60, group: 'seleção',
    description: 'Duração máxima de cada corte, em segundos.',
  },
  {
    key: 'target_duration', flag: '--target', kind: 'float', min: 5, max: 600, default: 42, group: 'seleção',
    description: 'Duração ideal que o seletor persegue, em segundos. Deve ficar entre min e max.',
  },
  {
    key: 'min_score', flag: '--min-score', kind: 'float', min: 0, max: 100, default: 0, group: 'seleção',
    description: 'Portão de qualidade 0-100: cortes abaixo disso são descartados. 0 desliga o portão.',
  },
  {
    key: 'engine', flag: '--engine', kind: 'enum', values: ['hybrid', 'audio', 'transcript'], default: 'hybrid', group: 'seleção',
    description: 'hybrid = transcrição + energia (padrão). audio = pula a transcrição, muito mais rápido e menos preciso. transcript = só texto.',
  },
  {
    key: 'min_gap', flag: '--min-gap', kind: 'float', min: 0, max: 120, default: 6, group: 'seleção',
    description: 'Silêncio mínimo mantido entre dois cortes aceitos, em segundos.',
  },

  // ---- enquadramento -----------------------------------------------------
  {
    key: 'layout', flag: '--layout', kind: 'enum', values: ['focus', 'center', 'blur', 'fit'], default: 'focus', group: 'enquadramento',
    description: 'Como recortar para vertical. focus = detecta rosto (Haar cascade do OpenCV, já instalado e ativo). center = centro fixo. blur = fundo desfocado. fit = cabe inteiro com barras.',
  },
  {
    key: 'reframe_zoom', flag: '--reframe-zoom', kind: 'float', min: 1, max: 3, group: 'enquadramento',
    description: 'Zoom do recorte. 1 ou maior. Ausente = sem zoom.',
  },
  {
    key: 'vertical', flag: '--no-vertical', kind: 'boolean', default: true, defaultOn: true, group: 'enquadramento',
    description: 'Formato vertical 1080x1920. Ligado por padrão. Enviar false gera o vídeo na proporção original.',
  },

  // ---- legenda -----------------------------------------------------------
  {
    key: 'caption_style', flag: '--caption-style', kind: 'enum', values: ['karaoke', 'block', 'none'], default: 'karaoke', defaultOn: true, group: 'legenda',
    description: 'Estilo base da legenda. karaoke = palavra destacada. block = caixa de frase. none = sem legenda (use só se pedirem explicitamente "sem legenda").',
  },
  {
    key: 'caption_preset', flag: '--caption-preset', kind: 'enum', values: CAPTION_PRESETS, default: 'karaoke', defaultOn: true, group: 'legenda',
    description: 'Visual pronto da legenda (fonte, cor, caixa e destaque). ' +
      Object.entries(CAPTION_PRESET_DESCRIPTIONS).map(([k, v]) => `${k}: ${v}`).join(' | '),
  },
  {
    key: 'font_size', flag: '--font-size', kind: 'int', min: 20, max: 200, group: 'legenda',
    description: 'Tamanho da fonte da legenda. Ausente = herda do preset.',
  },
  {
    key: 'caption_words_per_line', flag: '--words-per-line', kind: 'int', min: 1, max: 10, group: 'legenda',
    description: 'Palavras por linha de legenda. Ausente = herda do preset.',
  },
  {
    key: 'caption_margin_v', flag: '--caption-margin', kind: 'int', min: 0, max: 1500, group: 'legenda',
    description: 'Margem inferior da legenda em pixels. 640 mantém o texto fora da zona de botões do TikTok.',
  },
  {
    key: 'uppercase_captions', flag: '--uppercase', kind: 'boolean', group: 'legenda',
    description: 'Legenda em MAIÚSCULAS. Ausente = herda do preset.',
  },
  {
    key: 'highlight_color', flag: '--highlight-color', kind: 'string', group: 'legenda',
    description: 'Cor de destaque da legenda e do título, formato ASS &HAABBGGRR (ex.: &H0000FFFF para amarelo). Prefira escolher pelo preset.',
  },
  {
    key: 'caption_box_theme', flag: '--caption-box-theme', kind: 'enum', values: ['light', 'dark'], group: 'legenda',
    description: 'Tema claro/escuro da caixa da legenda. Ausente = cores do preset.',
  },

  // ---- gancho / título ---------------------------------------------------
  {
    key: 'headline_seconds', flag: '--headline-seconds', kind: 'float', min: 0, max: 15, default: 0, group: 'gancho',
    description: 'Duração do título de gancho queimado no topo, em segundos. DESLIGADO por padrão (0). Peça só quando o usuário quiser capa.',
  },
  {
    key: 'headline_text', flag: '--headline', kind: 'string', max: 120, group: 'gancho',
    description: 'Texto fixo do título de gancho. Ausente = gerado automaticamente da abertura do corte.',
  },
  {
    key: 'headline_font_size', flag: '--headline-font-size', kind: 'int', min: 20, max: 200, group: 'gancho',
    description: 'Tamanho da fonte do título de gancho.',
  },

  // ---- visual ------------------------------------------------------------
  {
    key: 'progress_bar', flag: '--progress-bar', kind: 'boolean', default: false, group: 'visual',
    description: 'Desenha barra de progresso no topo. Desligado por padrão.',
  },
  {
    key: 'jump_cut', flag: '--jump-cut', kind: 'boolean', default: false, group: 'visual',
    description: 'Remove os silêncios internos do corte. Desligado por padrão.',
  },
  {
    key: 'loudnorm', flag: '--no-loudnorm', kind: 'boolean', default: true, defaultOn: true, group: 'visual',
    description: 'Normaliza o áudio em -14 LUFS. Ligado por padrão; enviar false desliga.',
  },
  {
    key: 'template', flag: '--template', kind: 'enum', values: CAPTION_TEMPLATES, group: 'visual',
    description: 'Composição de tela. full-frame = vídeo ocupa o quadro inteiro (padrão). split-card = faixas divididas.',
  },

  // ---- processamento -----------------------------------------------------
  {
    key: 'whisper_model', flag: '--model', kind: 'enum', values: ['tiny', 'base', 'small', 'medium', 'large-v3'], default: 'small', group: 'processamento',
    description: 'Modelo do Whisper. Maior = mais preciso e MUITO mais lento em CPU. tiny/base para teste rápido.',
  },
  {
    key: 'language', flag: '--language', kind: 'string', max: 8, group: 'processamento',
    description: 'Força o idioma da transcrição (ex.: pt). Omitir = detecção automática.',
  },
  {
    key: 'ranker', flag: '--ranker', kind: 'enum', values: ['none', 'llm'], default: 'none', group: 'processamento',
    description: 'llm reavalia os melhores candidatos com um modelo de linguagem. Exige chave de API configurada; nenhum por padrão.',
  },
  {
    key: 'workers', flag: '--workers', kind: 'int', min: 1, max: 8, default: 2, group: 'processamento',
    description: 'Quantos cortes renderizar em paralelo. Cada worker consome memória.',
  },
  {
    key: 'dry_run', flag: '--plan-only', kind: 'boolean', default: false, group: 'processamento',
    description: 'true = só planeja os cortes, sem baixar seções nem renderizar. ATENÇÃO: ainda baixa o áudio e transcreve.',
  },
  {
    // Esta entrada existe por causa de uma falha MEDIDA, não por completude.
    // No modo `sections` (padrão) o yt-dlp baixa só as faixas de tempo de cada
    // corte, e o YouTube passou a responder 403 Forbidden em parte dessas
    // requisições com Range. Resultado real: clip_01 corta, clip_02 morre com
    // "ffmpeg exited with code 3436169992". O próprio yt-dlp imprime a saída:
    // "Retry, or use --download-mode full to download the whole video at once."
    //
    // Sem esta chave o agente não tinha como aplicar o conserto que o erro
    // sugere — a flag existia no CLI e não existia aqui. O usuário só descobria
    // isso lendo o log e rodando o comando copiado no terminal.
    key: 'download_mode', flag: '--download-mode', kind: 'enum', values: ['sections', 'full'],
    default: 'sections', group: 'processamento',
    description:
      'sections (padrão) baixa só as faixas de cada corte — rápido, mas o YouTube ' +
      'às vezes responde 403 e o render morre no meio. Use full se aparecer erro ' +
      '403 ou "yt-dlp failed to cut a section": baixa o vídeo inteiro de uma vez, ' +
      'mais lento e mais confiável.',
  },
] as const;

/** Índice por chave, para validação O(1). */
const BY_KEY = new Map(CLIP_OPTIONS.map((option) => [option.key, option]));

/**
 * Por que a recusa aconteceu.
 *
 * `invalido` é o caso comum: o payload está errado e o agente conserta sozinho.
 * `em-andamento` é outra coisa completamente — o payload está certo e o
 * problema é de ESTADO (já existe um render rodando). O agente precisa reagir
 * diferente: insistir não adianta e reenviar o plano é ruído. Sem esta marca, a
 * única saída seria casar a mensagem por texto, que quebra na primeira vez que
 * alguém reescreve a frase.
 */
export type ParamErrorCode = 'invalido' | 'em-andamento';

export class ParamError extends Error {
  readonly code: ParamErrorCode;

  constructor(message: string, code: ParamErrorCode = 'invalido') {
    super(message);
    this.code = code;
  }
}

/** Payload aceito: URL + parâmetros validados. */
export interface ClipRequest {
  url: string;
  params: Record<string, string | number | boolean>;
}

function isUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:';
  } catch {
    return false;
  }
}

/**
 * Valida a intenção do agente e devolve um pedido normalizado.
 * Lança ParamError com mensagem acionável em qualquer divergência — a mensagem
 * volta para o agente, que então corrige o JSON em vez de insistir.
 */
export function validateRequest(raw: unknown): ClipRequest {
  if (typeof raw !== 'object' || raw === null) {
    throw new ParamError('O payload precisa ser um objeto JSON.');
  }
  const input = raw as Record<string, unknown>;
  const url = input.url;
  if (typeof url !== 'string' || !isUrl(url)) {
    throw new ParamError(
      'O campo "url" é obrigatório e precisa ser uma URL http(s) válida do vídeo.',
    );
  }

  const rawParams = input.params ?? {};
  if (typeof rawParams !== 'object' || rawParams === null || Array.isArray(rawParams)) {
    throw new ParamError('O campo "params" precisa ser um objeto.');
  }

  const unknown = Object.keys(rawParams as Record<string, unknown>).filter(
    (key) => !BY_KEY.has(key),
  );
  if (unknown.length > 0) {
    // As chaves válidas vão INLINE. Antes a mensagem dizia "Use apenas as
    // chaves listadas em clip_get_options", o que mandava o modelo de volta a
    // uma consulta — uma ida e volta a mais, e o caminho em que ele encerrava
    // o turno sem entregar nada. Com a lista aqui, ele se corrige sozinho numa
    // única chamada a clip_plan.
    throw new ParamError(
      `Parâmetro(s) não permitido(s): ${unknown.join(', ')}. ` +
        `Chaves válidas: ${[...BY_KEY.keys()].join(', ')}.`,
    );
  }

  const params: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(rawParams as Record<string, unknown>)) {
    const option = BY_KEY.get(key)!;
    params[key] = coerce(option, value);
  }

  // Coerência entre as três durações. O CLI aceita valores contraditórios e
  // seleciona janelas esquisitas em silêncio; aqui é erro explícito.
  const min = num(params.min_duration);
  const max = num(params.max_duration);
  const target = num(params.target_duration);
  if (min !== undefined && max !== undefined && min > max) {
    throw new ParamError(`min_duration (${min}) não pode ser maior que max_duration (${max}).`);
  }
  if (target !== undefined) {
    const low = min ?? 30;
    const high = max ?? 60;
    if (target < low || target > high) {
      throw new ParamError(
        `target_duration (${target}) precisa ficar entre min_duration (${low}) e max_duration (${high}).`,
      );
    }
  }

  return { url, params };
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

function coerce(option: ClipOption, value: unknown): string | number | boolean {
  switch (option.kind) {
    case 'boolean': {
      if (typeof value !== 'boolean') {
        throw new ParamError(`"${option.key}" precisa ser true ou false.`);
      }
      return value;
    }
    case 'int':
    case 'float': {
      const parsed = typeof value === 'number' ? value : Number(value);
      if (typeof value === 'boolean' || !Number.isFinite(parsed)) {
        throw new ParamError(`"${option.key}" precisa ser um número.`);
      }
      if (option.kind === 'int' && !Number.isInteger(parsed)) {
        throw new ParamError(`"${option.key}" precisa ser um número inteiro.`);
      }
      if (option.min !== undefined && parsed < option.min) {
        throw new ParamError(`"${option.key}" precisa ser >= ${option.min}.`);
      }
      if (option.max !== undefined && parsed > option.max) {
        throw new ParamError(`"${option.key}" precisa ser <= ${option.max}.`);
      }
      return parsed;
    }
    case 'enum': {
      if (typeof value !== 'string' || !option.values?.includes(value)) {
        throw new ParamError(
          `"${option.key}" aceita apenas: ${option.values?.join(', ')}. Recebi: ${JSON.stringify(value)}.`,
        );
      }
      return value;
    }
    case 'string': {
      if (typeof value === 'string' && option.max !== undefined && value.length > option.max) {
        throw new ParamError(`"${option.key}" excede ${option.max} caracteres.`);
      }
      if (typeof value !== 'string') {
        throw new ParamError(`"${option.key}" precisa ser texto.`);
      }
      return value;
    }
    default: {
      throw new ParamError(`Tipo de parâmetro desconhecido para "${option.key}".`);
    }
  }
}

/**
 * Converte os parâmetros validados em argv para o CLI.
 *
 * Regras de segurança:
 *  - nada aqui passa por shell; o retorno é um array entregue direto ao spawn;
 *  - flags booleanas "defaultOn" (vertical, loudnorm, progress_bar...) só viram
 *    argv quando o valor DIVERGE do padrão, evitando flags redundantes;
 *  - os valores numéricos são serializados com String(), sem concatenação em
 *    string de comando, então não há superfície de injeção.
 */
export function toArgv(request: ClipRequest): string[] {
  const argv: string[] = ['-m', 'viralclipper', request.url];

  for (const option of CLIP_OPTIONS) {
    if (!(option.key in request.params)) continue;
    const value = request.params[option.key];
    if (!option.flag) continue;

    if (option.kind === 'boolean') {
      const atDefault = option.default !== undefined && value === option.default;
      if (atDefault) continue;
      // Flags no CLI são negativas quando o padrão é ligado (--no-vertical).
      if (option.flag.startsWith('--no-')) {
        if (value === false) argv.push(option.flag);
      } else if (value === true) {
        argv.push(option.flag);
      }
      continue;
    }

    argv.push(option.flag, String(value));
  }

  return argv;
}

/** Resumo legível do que vai rodar, para o card de confirmação. */
export function describeRequest(request: ClipRequest): string[] {
  const lines: string[] = [`URL: ${request.url}`];
  for (const option of CLIP_OPTIONS) {
    if (!(option.key in request.params)) continue;
    const value = request.params[option.key];
    const atDefault = option.default !== undefined && value === option.default;
    lines.push(`${option.key} = ${String(value)}${atDefault ? ' (padrão)' : ''}`);
  }
  return lines;
}

/** O cardápio completo, no formato que o agente consome. */
export function optionsForAgent() {
  return CLIP_OPTIONS.map((option) => ({
    key: option.key,
    tipo: option.kind,
    valores: option.values ?? undefined,
    faixa: option.min !== undefined ? [option.min, option.max] : undefined,
    padrao: option.default,
    ligado_por_padrao: option.defaultOn === true,
    grupo: option.group,
    descricao: option.description,
  }));
}
