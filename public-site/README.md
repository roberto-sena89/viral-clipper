# Site público do Viral Clipper

Este diretório contém um site estático separado da interface operacional. O build gera HTML, CSS, fontes locais, favicon, robots.txt e sitemap.xml; ele não inclui a API local de processamento, arquivos do computador ou rotas de execução/aprovação do Studio.

## Gerar os arquivos

Defina a origem HTTPS pública real antes do build. Não use localhost, endereço privado ou URL de desenvolvimento:

    $env:VIRAL_CLIPPER_SITE_URL = "https://seu-dominio.example"
    python public-site/build.py

O destino padrão é public-site/dist. Para escolher outro diretório, passe --output-dir. No Vercel, configure public-site como Root Directory; o vercel.json executa o build e publica somente dist. O build usa VERCEL_PROJECT_PRODUCTION_URL no Vercel ou VIRAL_CLIPPER_SITE_URL em outros ambientes e recusa URL sem HTTPS ou com caminho, query e fragmento.

Canonicals, sitemap e referência do sitemap em robots.txt são gerados a partir da origem informada. Não publique com o domínio de exemplo acima. CSS, fontes e favicon ficam em `assets/` e são copiados para `dist/assets/` pelo mesmo build.

## Separação operacional

Hospede este conteúdo estático em um domínio público. Mantenha web/server.py e o Studio vinculados a 127.0.0.1 ou proteja-os com autenticação e controles de acesso antes de expor. Não use o servidor de desenvolvimento do app como servidor deste site público.
