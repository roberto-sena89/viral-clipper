/**
 * Leitor de `.env` sem dependencia externa.
 *
 * Por que nao usar o pacote `dotenv`: a unica coisa necessaria aqui e ler um
 * arquivo de chave=valor e respeitar o que ja existe no processo. Um pacote a
 * mais significa mais uma coisa para manter atualizada, e o projeto ja tem
 * duas dependencias nativas que dao trabalho no Windows (better-sqlite3,
 * esbuild). Nao vale por 30 linhas.
 *
 * REGRA DE PRECEDENCIA — o ponto do modulo:
 * variavel ja definida no processo VENCE o arquivo. Isso e o que permite
 * `OPENROUTER_API_KEY=... npm run dev` sobrescrever o .env para um teste
 * pontual, e o que faz o ambiente de CI funcionar sem editar arquivo.
 * O contrario (arquivo vence) produziria o pior tipo de bug de configuracao:
 * a chave do shell e ignorada em silencio.
 */
import fs from 'node:fs';

/**
 * Carrega um arquivo `.env` no `process.env`.
 *
 * @param file Caminho absoluto do arquivo. Ausente nao e erro: rodar sem `.env`
 *             e um cenario legitimo (todas as chaves podem vir do ambiente).
 * @returns Lista das chaves efetivamente definidas por este arquivo. Util para
 *          reportar o que foi carregado sem imprimir valor nenhum.
 */
export function loadDotEnv(file: string): string[] {
  if (!fs.existsSync(file)) return [];

  const applied: string[] = [];
  const raw = fs.readFileSync(file, 'utf-8');

  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;

    const key = trimmed.slice(0, eq).trim();
    if (!key) continue;

    let value = trimmed.slice(eq + 1).trim();

    // Aspas envolventes sao sintaxe, nao conteudo. `KEY="a b"` vale `a b`.
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    // Nao sobrescreve: o ambiente do processo tem precedencia (ver comentario
    // do topo). Sem esta guarda, subir o servidor com uma chave exportada
    // seria impossivel sem editar o arquivo.
    if (process.env[key] !== undefined) continue;

    process.env[key] = value;
    applied.push(key);
  }

  return applied;
}

/**
 * Classifica o estado das chaves que um `.env` declara.
 *
 * Serve para diagnostico: distinguir "a chave nao esta no arquivo" de "a chave
 * esta no arquivo mas vazia". Sao problemas diferentes — o primeiro e editar o
 * arquivo, o segundo e preencher um campo que ja existe. Reportar os dois como
 * "ausente" faz perder tempo procurando a linha certa.
 *
 * @param file   Caminho do `.env`, que pode nao existir.
 * @param wanted Chaves de interesse. Quem decide a lista e o chamador, porque
 *               so ele sabe quais chaves importam para o seu subsistema.
 */
export function inspectDotEnv(
  file: string,
  wanted: string[],
): { missing: string[]; empty: string[]; filled: string[] } {
  const missing: string[] = [];
  const empty: string[] = [];
  const filled: string[] = [];

  const inFile = new Map<string, string>();
  if (fs.existsSync(file)) {
    const raw = fs.readFileSync(file, 'utf-8');
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eq = trimmed.indexOf('=');
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      let value = trimmed.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      inFile.set(key, value);
    }
  }

  for (const key of wanted) {
    // O ambiente do processo conta como preenchido, porque e ele que vence.
    const fromEnv = process.env[key];
    if (fromEnv !== undefined && fromEnv !== '') {
      filled.push(key);
      continue;
    }
    if (!inFile.has(key)) {
      missing.push(key);
    } else if (inFile.get(key) === '') {
      empty.push(key);
    } else {
      filled.push(key);
    }
  }

  return { missing, empty, filled };
}

