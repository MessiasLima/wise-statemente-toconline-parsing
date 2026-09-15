# Importação de Extratos Wise no TOConline

Converte extratos CSV da Wise para o formato XLSX de importação de movimentos do `TOConline`.

## Estrutura

Na raiz do repositório devem existir apenas:

- `create_import_xlsx.py`
- `importacao_movimentos.xlsx`
- `input/`
- `output/`
- `README.md`
- `.gitignore`

O diretório `.git/` também é permitido por conter os metadados do Git. Os ficheiros financeiros em `input/` e `output/` são ignorados pelo Git.

## Utilização

1. Exporte o extrato da Wise em formato CSV.
2. Coloque um ou mais ficheiros CSV na pasta `input/`.
3. Execute:

   ```bash
   python3 create_import_xlsx.py
   ```

4. Consulte os ficheiros gerados na pasta `output/`. Cada ficheiro mantém o nome original e altera apenas a extensão de `.csv` para `.xlsx`.

O script processa todos os CSV existentes em `input/` utilizando `importacao_movimentos.xlsx` como modelo. Os movimentos são escritos do mais antigo para o mais recente, as referências de pagamento são mantidas na descrição, os valores com sinal são preservados e é validada a reconciliação dos saldos inicial e final.

## Verificação de segurança

Antes de processar os ficheiros, o script termina se encontrar na raiz um item, ligação simbólica, diretório ou extensão não autorizada. A lista permitida inclui o conversor, o modelo, o README, o `.gitignore`, `input/`, `output/` e `.git/`. A pasta `input/` pode conter apenas ficheiros CSV e `.gitkeep`; a pasta `output/` pode conter apenas ficheiros XLSX e `.gitkeep`.

Não desative esta verificação nem faça commit de ficheiros financeiros provenientes de qualquer uma das pastas de dados.
