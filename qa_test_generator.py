"""
Gera código de testes automatizados a partir de uma função/método extraído
de um repositório de código-fonte, usando GPT-4o.

Suporta:
  - Java 8+  → JUnit 4 ou JUnit 5 (com Mockito)
  - JavaScript/TypeScript → Jest (com ou sem React Testing Library)
  - APEX (Salesforce) → @isTest class

Entrada: resultado de qa_code_analyzer.fetch_function_from_repo()
Saída:   string com o código do arquivo de teste completo
"""

import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Prompts por framework
# ---------------------------------------------------------------------------

_PROMPT_JUNIT5 = """\
Você é um engenheiro de qualidade especialista em Java 8 e JUnit 5.

Analise o método abaixo e gere uma classe de teste JUnit 5 COMPLETA, pronta para uso.

**Contexto do arquivo (imports e classe):**
```java
{file_context}
```

**Método a ser testado:**
```java
{function_code}
```

**Parâmetros de exemplo fornecidos pelo usuário:**
{example_params_section}

**Diretrizes obrigatórias:**
- Classe de teste: `{class_name}Test`
- Anote a classe com `@ExtendWith(MockitoExtension.class)` se usar mocks
- Cada método de teste com `@Test` e nome descritivo em snake_case ou camelCase
- Use `@DisplayName("descrição legível")` em cada teste
- Cubra obrigatoriamente: caminho feliz, valores nulos/vazios, exceções esperadas, edge cases
- Use `Assertions.assertEquals`, `assertThrows`, `assertNotNull` do JUnit 5
- Use Mockito (`@Mock`, `@InjectMocks`, `when(...).thenReturn(...)`) para dependências externas
- Importe tudo que for necessário (não assuma imports implícitos)
- NÃO use JUnit 4 (`@RunWith`, `Assert.*`)
- Gere APENAS o código Java puro, sem blocos markdown

**Retorne apenas o código da classe de teste completa, começando com `package` ou `import`.**
"""

_PROMPT_JUNIT4 = """\
Você é um engenheiro de qualidade especialista em Java 8 e JUnit 4.

Analise o método abaixo e gere uma classe de teste JUnit 4 COMPLETA, pronta para uso.

**Contexto do arquivo (imports e classe):**
```java
{file_context}
```

**Método a ser testado:**
```java
{function_code}
```

**Parâmetros de exemplo fornecidos pelo usuário:**
{example_params_section}

**Diretrizes obrigatórias:**
- Classe de teste: `{class_name}Test`
- Anote com `@RunWith(MockitoJUnitRunner.class)` se usar mocks
- Cada método de teste com `@Test` e nome descritivo
- Cubra: caminho feliz, valores nulos, exceções esperadas, edge cases
- Use `Assert.assertEquals`, `Assert.assertNotNull`, `@Test(expected=...)` do JUnit 4
- Use Mockito para dependências externas
- Importe tudo que for necessário
- Gere APENAS o código Java puro, sem blocos markdown

**Retorne apenas o código da classe de teste completa.**
"""

_PROMPT_JEST = """\
Você é um engenheiro de qualidade especialista em JavaScript/TypeScript e Jest.

Analise a função abaixo e gere um arquivo de teste Jest COMPLETO, pronto para uso.

**Contexto do arquivo (imports):**
```typescript
{file_context}
```

**Função a ser testada:**
```typescript
{function_code}
```

**Parâmetros de exemplo fornecidos pelo usuário:**
{example_params_section}

**Diretrizes obrigatórias:**
- Nome do arquivo de teste: `{function_name}.test.{ext}`
- Use `describe('{function_name}', () => {{ ... }})` como bloco raiz
- Cada teste com `it('descrição legível', () => {{ ... }})`
- Cubra: caminho feliz, valores nulos/undefined, exceções/rejeições, edge cases
- Use `expect(...).toBe(...)`, `toEqual`, `toThrow`, `resolves`, `rejects`
- Use `jest.mock(...)` para dependências externas
- Importe a função corretamente (considere export default vs named export)
- Gere APENAS o código TypeScript/JavaScript puro, sem blocos markdown

**Retorne apenas o código do arquivo de teste completo.**
"""

_PROMPT_JEST_RTL = """\
Você é um engenheiro de qualidade especialista em React, Jest e React Testing Library.

Analise o componente/função abaixo e gere um arquivo de teste COMPLETO com Jest + RTL.

**Contexto do arquivo (imports):**
```tsx
{file_context}
```

**Componente/função a ser testado:**
```tsx
{function_code}
```

**Parâmetros de exemplo fornecidos pelo usuário:**
{example_params_section}

**Diretrizes obrigatórias:**
- Nome do arquivo: `{function_name}.test.{ext}`
- Importe: `import {{ render, screen, fireEvent, waitFor }} from '@testing-library/react'`
- Use `describe('{function_name}', () => {{ ... }})` como bloco raiz
- Cubra: renderização básica (snapshot ou queries), interações do usuário, estados de erro/loading, props obrigatórias vs opcionais
- Use `screen.getByText`, `getByRole`, `getByTestId` para queries
- Use `fireEvent.click`, `fireEvent.change` para interações
- Mock de hooks e chamadas de API com `jest.mock`
- Gere APENAS código puro sem blocos markdown

**Retorne apenas o código do arquivo de teste completo.**
"""

_PROMPT_APEX = """\
Você é um engenheiro de qualidade especialista em Salesforce Apex.

Analise o método abaixo e gere uma classe de teste Apex COMPLETA, pronta para uso.

**Contexto do arquivo:**
```java
{file_context}
```

**Método a ser testado:**
```java
{function_code}
```

**Parâmetros de exemplo fornecidos pelo usuário:**
{example_params_section}

**Diretrizes obrigatórias:**
- Anote a classe com `@isTest`
- Use `Test.startTest()` e `Test.stopTest()` para delimitar a execução
- Use `System.assertEquals`, `System.assertNotEquals`, `System.assert` para assertions
- Crie dados de teste com `TestDataFactory` ou inline com `insert`
- Para callouts externos: use `Test.setMock(HttpCalloutMock.class, ...)`
- Cubra: caminho feliz, dados nulos, exceções DmlException/QueryException
- Anote cada método de teste com `@isTest`
- Gere APENAS o código Apex puro, sem blocos markdown

**Retorne apenas o código da classe de teste completa.**
"""

_PROMPTS = {
    "junit5": _PROMPT_JUNIT5,
    "junit4": _PROMPT_JUNIT4,
    "jest": _PROMPT_JEST,
    "jest-rtl": _PROMPT_JEST_RTL,
    "apex-test": _PROMPT_APEX,
}

# Extensão do arquivo de saída por framework
_OUTPUT_EXT = {
    "junit5": ".java",
    "junit4": ".java",
    "jest": ".test.js",
    "jest-rtl": ".test.tsx",
    "apex-test": "Test.cls",
}


# ---------------------------------------------------------------------------
# Geração
# ---------------------------------------------------------------------------

def generate_tests(
    function_name: str,
    function_code: str,
    file_context: str,
    language: str,
    framework: str,
    example_params: str = "",
) -> dict:
    """
    Gera o código de teste e retorna:
    {
        "test_code": "...",
        "filename": "NomeFuncaoTest.java",
        "framework": "junit5",
        "language": "java",
    }
    """
    prompt_template = _PROMPTS.get(framework)
    if not prompt_template:
        raise ValueError(f"Framework '{framework}' não suportado. Disponíveis: {list(_PROMPTS)}")

    class_name = _to_class_name(function_name)
    ext = "ts" if language == "typescript" else "js"
    example_params_section = (
        f"```\n{example_params}\n```\nUse esses valores como base para os casos de teste."
        if example_params else
        "Nenhum exemplo fornecido — infira valores razoáveis a partir da assinatura da função."
    )

    prompt = prompt_template.format(
        function_code=function_code,
        file_context=file_context or "(sem contexto adicional)",
        function_name=function_name,
        class_name=class_name,
        ext=ext,
        example_params_section=example_params_section,
    )

    test_code = _call_llm(prompt)
    filename = _build_filename(function_name, class_name, framework, ext)

    return {
        "test_code": test_code,
        "filename": filename,
        "framework": framework,
        "language": language,
    }


def _call_llm(prompt: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    code = response.choices[0].message.content.strip()
    # Remove fences markdown se o LLM incluir
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:])
        if code.endswith("```"):
            code = code[:-3].rstrip()
        elif "\n```" in code:
            code = code[: code.rfind("\n```")]
    return code


def _to_class_name(name: str) -> str:
    """calcularDesconto → CalcularDesconto  |  calcular_desconto → CalcularDesconto"""
    # camelCase → PascalCase
    pascal = name[0].upper() + name[1:] if name else name
    # snake_case → PascalCase
    pascal = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), pascal)
    return pascal


def _build_filename(function_name: str, class_name: str, framework: str, ext: str) -> str:
    if framework in ("junit5", "junit4"):
        return f"{class_name}Test.java"
    elif framework == "apex-test":
        return f"{class_name}Test.cls"
    elif framework == "jest-rtl":
        return f"{function_name}.test.tsx"
    else:
        return f"{function_name}.test.{ext}"
