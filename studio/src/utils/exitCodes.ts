/**
 * O que cada código de saída do CLI significa, em uma frase.
 *
 * Existe porque "Processo finalizado com código 4" é verdadeiro e inútil: o
 * usuário teria de subir o log inteiro para descobrir que perdeu a legenda de
 * todos os cortes. O código é o único sinal que o painel recebe quando o run
 * termina sem erro fatal, então ele precisa vir traduzido.
 *
 * Espelha o `run_single` do `viralclipper/cli.py`. Se um código mudar lá, muda
 * aqui — e é por isso que o mapa é um só, compartilhado pelo log e pelo rodapé.
 */
const DESCRICOES: Record<number, string> = {
  0: 'Concluído com sucesso.',
  1: 'A execução falhou. Veja o log acima.',
  2: 'Erro de uso: algum argumento foi recusado antes de o trabalho começar.',
  3: 'Alguns cortes ficaram abaixo da duração mínima configurada.',
  4: 'Transcrição indisponível: os cortes saíram SEM legenda queimada e a seleção usou apenas a energia do áudio.',
  5: 'Outra execução já está usando esta pasta de saída. Espere ela terminar — ou pare-a — e rode de novo. Duas ao mesmo tempo escrevem os MESMOS arquivos e a última sobrescreve a outra sem erro nenhum.',
  130: 'Execução interrompida (Ctrl+C).',
};

/** `null` quando não houve código (processo morto por sinal). */
export function describeExitCode(code: number | null): string | null {
  if (code === null) return null;
  return DESCRICOES[code] ?? `Processo finalizado com código ${code}.`;
}
