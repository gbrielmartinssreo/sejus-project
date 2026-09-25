# Schema — Função `apresentar_documento_melhorado`

## Nível Superior
```json
{
  "numero": "string (obrigatório)",
  "ementa": "string (obrigatório)",
  "alteracoes": [...],
  "remocoes": [...],
  "adicoes_estruturais": [...],
  "plano_melhoria": [...],
  "lacunas_identificadas": [...]
}
```

---

## `alteracoes` (array de objetos)
```json
{
  "tipo": "'alterado' | 'corrigido'",
  "rotulo": "string (ex.: 'Art. 5º', '§ 2º do Art. 10')",
  "trecho_original": "string (cópia EXATA do original, com rótulo)",
  "novo_texto": "string (parágrafo inteiro já corrigido, com rótulo)",
  "detalhe": "string (o que mudou e por quê)",
  "categoria": "'safe_correction' | 'clarity_improvement' | 'structural_improvement' | 'normative_proposal' (obrigatório)",
  "cria_obrigacao": "boolean (obrigatório)",
  "risco_juridico": "'baixo' | 'medio' | 'alto' (obrigatório)",
  "requer_decisao_juridica": "boolean (derivado: true se normative_proposal OU cria_obrigacao)",
  "lastro": "string (opcional: referência ao ato base)",
  "origem": "'origem_analise_aprovada' | 'iniciativa_modelo'"
}
```

---

## `remocoes` (array de objetos)
```json
{
  "rotulo": "string",
  "trecho_original": "string (cópia EXATA)",
  "detalhe": "string (motivo da remoção)",
  "categoria": "'safe_correction' | 'clarity_improvement' | 'structural_improvement' | 'normative_proposal' (obrigatório)",
  "cria_obrigacao": "boolean (obrigatório, geralmente false)",
  "risco_juridico": "'baixo' | 'medio' | 'alto' (obrigatório)",
  "requer_decisao_juridica": "boolean",
  "lastro": "string",
  "origem": "'origem_analise_aprovada' | 'iniciativa_modelo'"
}
```

---

## `adicoes_estruturais` (array de objetos)
```json
{
  "o_que": "string (ex.: 'Art. 10-A', '§ 3º do Art. 5')",
  "texto": "string (texto COMPLETO e autônomo do novo dispositivo, obrigatório)",
  "posicao": "string (ex.: 'após Art. 10', 'antes do Capítulo II', 'ao final do Art. 5')",
  "fundamento": "string (base no PRÓPRIO documento ou no acervo recuperado)",
  "detalhe": "string (o que a adição resolve e por quê)",
  "categoria": "'structural_improvement' | 'normative_proposal' (obrigatório)",
  "cria_obrigacao": "boolean (obrigatório)",
  "risco_juridico": "'baixo' | 'medio' | 'alto' (obrigatório)",
  "requer_decisao_juridica": "boolean (derivado)",
  "lastro": "string",
  "origem": "'iniciativa_modelo'"  // adições estruturais são sempre iniciativa do modelo
}
```

---

## `plano_melhoria` (array de objetos) — **NOVO**
```json
{
  "achado_id": "string (ID da oportunidade ou apontamento, ex.: 'op-1', 'ap-3')",
  "achado": "string (texto do achado/oportunidade)",
  "categoria": "string (categoria declarada ou resultante)",
  "risco_juridico": "string",
  "decisao": "'aplicar' | 'propor' | 'descartar'",
  "dispositivo_alvo": "string (rótulo do dispositivo afetado)",
  "referencia": "string (referência declarada no plano)",
  "fundamento": "string (fundamento declarado)",
  "relacao_com_o_achado": "string (como a mudança resolve o achado)",
  "status": "'aplicado' | 'pendente' | 'nao_aplicado' | 'falhou'",
  "status_detalhe": "string (explicação do status)",
  "motivo_canonico": "string (quando nao_aplicado: um dos 6 motivos canônicos)"
}
```

**Regras do plano:**
- Uma linha por achado/oportunidade avaliada
- `status = "aplicado"` ⇔ existe mudança correspondente no patch EFETIVO
- `status = "pendente"` ⇔ mudança existe mas `requer_decisao_juridica=True`
- `status = "nao_aplicado"` ⇔ declarado mas sem mudança no patch; `motivo_canonico` obrigatório
- `status = "falhou"` ⇔ declarado sem mudança e sem motivo canônico válido
- Oportunidades não declaradas aparecem como `"nao avaliada"`

---

## `lacunas_identificadas` (array de objetos)
```json
{
  "tema": "string (ex.: 'recurso_administrativo', 'prazo_validade')",
  "detalhe": "string (descrição da lacuna)",
  "fundamento_sugerido": "string (opcional: onde buscar precedente)"
}
```

---

## Mudanças vs Schema Anterior

| Campo | Antes | Agora |
|-------|-------|-------|
| `alteracoes[].categoria` | — | **obrigatório** |
| `alteracoes[].cria_obrigacao` | — | **obrigatório** |
| `alteracoes[].risco_juridico` | — | **obrigatório** |
| `remocoes[].categoria` | — | **obrigatório** |
| `remocoes[].cria_obrigacao` | — | **obrigatório** |
| `remocoes[].risco_juridico` | — | **obrigatório** |
| `adicoes_estruturais[].texto` | opcional | **obrigatório** |
| `adicoes_estruturais[].categoria` | — | **obrigatório** |
| `adicoes_estruturais[].cria_obrigacao` | — | **obrigatório** |
| `adicoes_estruturais[].risco_juridico` | — | **obrigatório** |
| `plano_melhoria` | — | **novo (array)** |
| `adicoes_estruturais.tipo` | "artigo" | aceita "paragrafo" também |