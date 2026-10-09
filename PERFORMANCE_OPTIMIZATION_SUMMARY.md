# Viral-Clipper Performance Optimization Summary

Performance-related help text has been enhanced to guide users toward better utilization of system resources.

## 1. Workers Optimization ✅
- **File modified**: `cli.py`
- **Changes**:
  - Enhanced help text for `--workers` argument
  - Added guidance on scaling workers based on available RAM:
    - "Aumente este valor em sistemas com muita RAM para melhor paralelismo"
    - Examples: "4 para 8GB RAM, 8 para 16GB RAM"
  - Maintains default of 2 workers for conservative memory usage

## 2. Transcription Cache Optimization ✅
- **File modified**: `cli.py`
- **Changes**:
  - Enhanced help text for `--cache-dir` argument
  - Added explicit mention that caching is enabled by default
  - Clarified benefit: "Habilitado por padrão para melhor performance em múltiplas execuções do mesmo vídeo"
  - Cache location: `output/cache/transcripts` (outside temporary work directory)

## 3. Whisper Model Selection Optimization ✅
- **File modified**: `cli.py`
- **Changes**:
  - Enhanced help text for `--model` argument
  - Added performance/accuracy tradeoff guidance:
    - "tiny (mais rápido, menos preciso), base, small (padrão), medium, large (mais preciso, mais lento)"
  - Helps users choose based on their performance/accuracy needs

## 4. Whisper Device Optimization ✅
- **File modified**: `cli.py`
- **Changes**:
  - Enhanced help text for `--device` argument
  - Added GPU acceleration guidance:
    - "auto (detecta automaticamente), cuda (GPU), cpu (apenas CPU)"
    - "Use 'cuda' para aceleração por GPU se disponível"
  - Enables users to leverage GPU for significantly faster transcription when available

## Usage Examples for Optimal Performance

### For Maximum Parallelism (High-RAM Systems)
```bash
python -m viralclipper <URL> --workers 8  # For 16GB+ RAM systems
```

### For Faster Transcription (with GPU)
```bash
python -m viralclipper <URL> --device cuda --model tiny
```

### For Balanced Quality/Speed
```bash
python -m viralclipper <URL> --model small  # Default, good balance
```

### For Maximum Accuracy (Slower)
```bash
python -m viralclipper <URL> --model large
```

### For Rapid Iteration (Using Cache)
```bash
# First run creates cache
python -m viralclipper <URL>
# Subsequent runs reuse cache for faster processing
python -m viralclipper <URL>
```

## Technical Notes

- **Worker Count**: Each worker runs a full ffmpeg encode, consuming significant RAM. The default of 2 prevents OOM on modest systems.
- **Transcription Cache**: Stored outside temporary work directory (`output/cache/transcripts`) to persist between runs of the same video.
- **Whisper Models**:
  - `tiny`: ~39x faster than large, but lower accuracy
  - `base`: Good balance for many use cases
  - `small`: Default, recommended for most users
  - `medium`/`large`: Higher accuracy for challenging audio
- **Device Selection**:
  - `auto`: Automatically detects CUDA if available
  - `cpu`: Forces CPU-only (useful for debugging)
  - `cuda`: Explicit GPU usage (requires CUDA-enabled Whisper installation)

These optimizations maintain backward compatibility while providing clear guidance for users to maximize performance based on their specific hardware and use case requirements.
