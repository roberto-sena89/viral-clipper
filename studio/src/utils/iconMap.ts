import {
  Bot,
  Code,
  Globe,
  Sparkles,
  FileText,
  Lightbulb,
  type LucideIcon,
} from 'lucide-react';

// Icon 映射
//
// O tipo TEM que ser `LucideIcon`, que é o que a própria lucide-react exporta.
// Um `React.ComponentType<{ size?: number; color?: string }>` parece equivalente,
// mas não é: os ícones da lucide são `ForwardRefExoticComponent<LucideProps>`
// (LucideProps estende SVGProps e traz size/strokeWidth/absoluteStrokeWidth),
// e o TS recusa a atribuição por variância das props.
export const ICON_MAP: Record<string, LucideIcon> = {
  Bot,
  Sparkles,
  Code,
  FileText,
  Globe,
  Lightbulb,
};
