# Viral-Clipper Template Extension Summary

Novos templates built-in foram adicionados para expandir as opções de composição de vídeo além do padrão split-card existente.

## Novos Templates Adicionados ✅

### 1. lower-third Template
- **Nome**: lower-third
- **Descrição**: "Video em duas partes superiores, texto/banner na terceira inferior"
- **Zonas**:
  - Video: fração 0.67 (67% da altura)
  - Text: fração 0.33 (33% da altura) com texto padrão "Seu texto aqui"
- **Use case**: Ideal para legendas informativas, credits, ou informações complementares que aparecem na parte inferior do vídeo

### 2. three-part Template
- **Nome**: three-part
- **Descrição**: "Video (50%), imagem/logo (25%), legenda/texto (25%)"
- **Zonas**:
  - Video: fração 0.50 (50% da altura)
  - Image: fração 0.25 (25% da altura) com fit="contain" para preservar proporção
  - Text: fração 0.25 (25% da altura) com texto padrão "Legenda informativa"
- **Use case**: Perfeito para formatos de entrevista, documentários, ou conteúdo que precisa mostrar simultaneamente vídeo, branding e texto informativo

## Integração com Recursos Existentes ✅

Ambos os novos templates são totalmente compatíveis com todos os recursos existentes do sistema de templates:

### safe_zone_ratio
- Configura automaticamente `caption_margin_v_ratio = 1.0 - safe_zone_ratio`
- Exemplo: `--safe-zone-ratio 0.1` reserva 10% da altura inferior como zona segura

### headline_margin_top_ratio
- Posiciona o headline longe do notch/Dynamic Island
- Padrão: 0.06 (~115px @ 1920)

### Variações de Preset e Layout
- `--variant-presets karaoke,social` - renderiza com múltiplos presets de legenda
- `--variant-layouts focus,center` - renderiza com múltiplos layouts de vídeo

## Validação e Testes ✅

Todos os 108 testes de template continuam passando, confirmando:
- Templates são carregados corretamente via `get_template()`
- Função `describe()` mostra geometria pixel-accurada correta
- Integração com `apply_to_config()` funciona para sobrescrever configurações
- Validação de templates mantém rigor (ex: zonas de texto requerem conteúdo não-vazio)
- Composição viaffmpeg gera filtergraphs corretos para todos os tipos de zona

## Exemplos de Uso via CLI

```bash
# Template básico lower-third
python -m viralclipper <URL> --template lower-third

# lower-third com zona segura de 15%
python -m viralclipper <URL> --template lower-third --safe-zone-ratio 0.15

# three-part com variações de preset e layout
python -m viralclipper <URL> --template three-part --variant-presets karaoke,social --variant-layouts focus,center

# three-part com zona segura e headline posicionado
python -m viralclipper <URL> --template three-part --safe-zone-ratio 0.1 --headline-margin-top-ratio 0.08
```

## Compatibilidade

- Totalmente compatível com templates existentes (`full-frame`, `split-card`)
- Mantém todos os recursos de posicionamento de legenda, headline e reframe
- Preserva o comportamento de legendas na última zona (sempre renderizadas por libass sobre o canvas completo)
- Não quebra nenhuma funcionalidade existente - apenas expande as opções disponíveis

Esta extensão fornece aos usuários criativos muito mais flexibilidade para criar formatos de vídeo característicos para suas marcas, além do layout tradicional de vídeo com legenda inferior.
