/**
 * Rotas HTTP do Studio: preflight, cardápio de parâmetros e execução do CLI.
 *
 * Montado por `registerStudioRoutes(app)` no fim de server/index.ts, para não
 * poluir o arquivo do template e deixar claro o que é código nosso.
 */

import type { Express, Request, Response } from 'express';

import {
  CLIP_OPTIONS,
  CAPTION_PRESETS,
  CAPTION_PRESET_DESCRIPTIONS,
  ParamError,
  optionsForAgent,
} from './clipOptions.js';
import { runPreflight } from './preflight.js';
import { DIRECTOR_SYSTEM_PROMPT } from './directorPrompt.js';
import {
  approvePlan,
  cancelRun,
  commitPlan,
  createPlan,
  getPlan,
  renderCommand,
  subscribe,
  type RunEvent,
} from './clipRunner.js';

/** Traduz erro de validação em resposta útil: a mensagem volta para o agente. */
function fail(res: Response, error: unknown, status = 400): void {
  if (error instanceof ParamError) {
    res.status(status).json({ ok: false, error: error.message });
    return;
  }
  res.status(500).json({ ok: false, error: error instanceof Error ? error.message : String(error) });
}

export function registerStudioRoutes(app: Express): void {
  /** Estado do ambiente. O frontend chama isto ao abrir e mostra um banner. */
  app.get('/api/studio/preflight', async (_req: Request, res: Response) => {
    try {
      const report = await runPreflight();
      res.json({ ok: report.ok, report });
    } catch (error) {
      fail(res, error, 500);
    }
  });

  /** Cardápio de parâmetros: os presets e o que cada um faz. */
  app.get('/api/studio/options', (_req: Request, res: Response) => {
    res.json({
      ok: true,
      options: optionsForAgent(),
      caption_presets: CAPTION_PRESETS,
      caption_preset_descriptions: CAPTION_PRESET_DESCRIPTIONS,
      /** O frontend usa isto como system prompt padrão de novas conversas. */
      director_prompt: DIRECTOR_SYSTEM_PROMPT,
    });
  });

  /**
   * Etapa 1 — plano. Valida a intenção do agente e devolve o comando exato
   * que rodaria, para o usuário aprovar. Nada é executado aqui.
   */
  app.post('/api/studio/plan', (req: Request, res: Response) => {
    try {
      const plan = createPlan(req.body);
      res.json({
        ok: true,
        plan_id: plan.id,
        url: plan.request.url,
        params: plan.request.params,
        command: renderCommand(plan),
        argv: plan.argv,
        summary: plan.summary,
        requires_approval: true,
      });
    } catch (error) {
      // O agente recebe esta mensagem e corrige o JSON, em vez de insistir.
      fail(res, error);
    }
  });

  /**
   * Aprovação. Vem da interface (clique humano) — o agente não tem rota para
   * aprovar sozinho, e é isso que sustenta o gate de confirmação.
   */
  app.post('/api/studio/approve/:planId', (req: Request, res: Response) => {
    try {
      const plan = approvePlan(req.params.planId);
      res.json({ ok: true, plan_id: plan.id, approved: true });
    } catch (error) {
      fail(res, error, 404);
    }
  });

  /** Etapa 2 — execução. Recusa plano não aprovado. */
  app.post('/api/studio/commit/:planId', (req: Request, res: Response) => {
    try {
      const { runId } = commitPlan(req.params.planId);
      res.json({ ok: true, run_id: runId });
    } catch (error) {
      fail(res, error);
    }
  });

  /**
   * Cancelamento — o freio.
   *
   * Sem esta rota, um render disparado por engano não tinha como ser parado
   * pela interface: `cancelRun` existia no `clipRunner` desde o começo, mas
   * nenhuma rota o expunha. Com `RUN_TIMEOUT_MS` de 6 horas, a única saída era
   * esperar (ou matar o processo do servidor à mão).
   *
   * 404 quando o runId não existe ou já terminou — não há o que cancelar.
   */
  app.post('/api/studio/cancel/:runId', (req: Request, res: Response) => {
    const { runId } = req.params;
    if (!cancelRun(runId)) {
      res.status(404).json({ ok: false, error: 'Execução não encontrada ou já terminou.' });
      return;
    }
    res.json({ ok: true, run_id: runId, cancelled: true });
  });

  /** Estado de um plano, para a interface não chutar. */
  app.get('/api/studio/plan/:planId', (req: Request, res: Response) => {
    const plan = getPlan(req.params.planId);
    if (!plan) {
      res.status(404).json({ ok: false, error: 'Plano não encontrado ou expirado.' });
      return;
    }
    res.json({
      ok: true,
      plan_id: plan.id,
      approved: plan.approved,
      consumed: plan.consumed,
      command: renderCommand(plan),
      summary: plan.summary,
    });
  });

  /**
   * SSE com a saída do CLI em tempo real. O frontend abre este canal quando
   * dispara a execução; as linhas chegam conforme o processo imprime.
   */
  app.get('/api/studio/events', (req: Request, res: Response) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders?.();

    res.write(`data: ${JSON.stringify({ type: 'hello' })}\n\n`);

    const unsubscribe = subscribe((event: RunEvent) => {
      res.write(`data: ${JSON.stringify(event)}\n\n`);
    });

    // Ping para proxies não fecharem o canal durante um render longo.
    const keepAlive = setInterval(() => {
      res.write(': ping\n\n');
    }, 20_000);

    req.on('close', () => {
      clearInterval(keepAlive);
      unsubscribe();
      res.end();
    });
  });

  /** Catálogo cru, para depuração e para o frontend montar formulários. */
  app.get('/api/studio/catalog', (_req: Request, res: Response) => {
    res.json({ ok: true, options: CLIP_OPTIONS });
  });
}
