# Gemini Flash 4.6 (debut temporal) — "dayz-mcp Optimization Pack (AI Native)" (2026-08-17)

> **Origen**: `Escritorio\OutputGemini.txt`, traido por el usuario el 17-08. Copiado verbatim abajo.
> **Lectura preliminar de Claude (no decision)**: Gemini NO leyo el repo (no tenia acceso): describe OTRO producto —
> un MCP de busqueda/validacion de la API Enforce (`dayz_search_api_symbol`, `dayz_get_class_details`,
> `dayz_validate_enforcescript`, `dayz_get_modding_hook_template`), ninguna de esas tools existe en dayz-mcp, que es
> control del juego (DayZDiag) por bridge. El contenido concreto es paja; los code fences anidados vienen rotos.
> **Grano rescatable (ideas, no texto)**: (1) un `llms.txt` en la raiz del repo publico (convencion AI-native: proposito,
> capacidades, indice de tools) — encaja con «un agente frio no cierra T1/T2» de las sondas; (2) schemas cerrados
> (`additionalProperties: false`, enums) pensados para modelos pequenos — es exactamente la linea del plan
> weak-agent de Grok; (3) un `system_prompt.md` como guia de consumidor. Para valer como lane, Gemini necesita el
> catalogo real (list_tools) y el README inline en el prompt; asi ciego, inventa.
> **Estado**: SIN TRIAR.

---

Aquí tienes los archivos preparados para que los puedas copiar y pegar directamente en tu proyecto.

---

### 1. `llms.txt`

*(Guárdalo en la raíz de tu repositorio)*

```markdown
# dayz-mcp

> Model Context Protocol (MCP) server for DayZ modding and Enforce Script development. Designed to expose DayZ base game API signatures, class hierarchies, and syntax validation tools to LLM clients.

## Core Capabilities
- Search DayZ Script API symbols (classes, methods, variables, enums, events).
- Inspect full class inheritance trees and available modding hooks (e.g., `PlayerBase`, `ItemBase`).
- Validate Enforce Script code against Enfusion Engine compiler rules and memory safety (`ref` usage).
- Generate clean `modded class` code templates with proper server/client context checks.

## Key Technical Specifications
- **Target Language:** Enforce Script (DayZ / Enfusion Engine).
- **Execution Contexts:** `1_Core`, `2_GameLib`, `3_Game`, `4_World`, `5_Mission`.
- **System Constraints:** No C++ STL, no raw pointers, GC-managed memory via `ref`, `modded class` patching pattern.

## MCP Tools Index
- `dayz_search_api_symbol`: Search methods and classes by query string.
- `dayz_get_class_details`: Retrieve full member list and parent hierarchy for a class.
- `dayz_validate_enforcescript`: Check code snippets for syntax errors or memory leaks.
- `dayz_get_modding_hook_template`: Get boilerplate code for common hooks (`EEInit`, `OnStoreSave`, `OnRPC`).

## Optional / Extended Documentation
- [MCP Tools Schema Spec](schemas/dayz_mcp_tools_schema.json): Complete JSON Schema definition for tool inputs/outputs.
- [Enforce Script Rules](system_prompt.md): System prompt guidelines for LLM code generation.

```

---

### 2. `system_prompt.md`

*(Guárdalo en la raíz de tu repositorio)*

```markdown
You are an expert developer specializing in DayZ modding and Enforce Script (Enfusion Engine). Your sole task is to generate valid, efficient, and memory-safe Enforce Script code. 

Strictly adhere to the following language constraints and rules to prevent C++/C# syntax hallucinations:

### 1. LANGUAGE & SYNTAX CORE RULES
- **No C++ Standard Library:** Do NOT use C++ headers (`#include <iostream>`), namespaces (`std::`), or std containers (`std::vector`, `std::map`, `std::string`).
- **No Manual Memory Allocation Keywords:** Never use `new` (except when instantiating Enforce classes via `new ClassName()`), `delete`, `malloc`, `free`, or raw pointers like `*` or `&`.
- **Arrays & Maps:** Use Enforce native containers: `array<typename>` and `map<keytype, valtype>`.
  - Example: `array<string> items = new array<string>;`
  - Method calls use dot notation: `items.Insert("Bandage");`, `items.Count();`
- **Class Extension / Modding Hooks:** Always use the `modded class ClassName` keyword when overriding or extending existing base game classes (e.g., `PlayerBase`, `ItemBase`).
- **Parent Calls:** Use `super.MethodName()` to call base implementation, NOT `BaseClass::MethodName()`.

### 2. MEMORY MANAGEMENT & POINTERS (`ref`)
- Enforce Script uses Reference Counting Garbage Collection.
- **`ref` Keyword:** Use `ref` ONLY when declaring a class member variable that OWNS the object instance to prevent premature garbage collection.
  - Example: `ref array<ref ItemBase> m_StorageArray;`
- Do NOT use `ref` on local variables inside method scopes.

### 3. DAYZ SPECIFIC PATTERNS
- **Class Modding Structure:**
  ```enforce
  modded class PlayerBase
  {
      override void EEInit()
      {
          super.EEInit();
          // Custom logic here
      }
  }

```

* **Null & Validity Checks:** Always validate objects before accessing methods using `GetGame()` or checking against `null`.
* Prefer checking `if (!item)` or `if (item == null)`.


* **RPC & Synchronization:** Explicitly check execution context using `GetGame().IsServer()`, `GetGame().IsClient()`, or `GetGame().IsDedicatedServer()` before executing server-side logic or client-side UI calls.

### 4. OUTPUT FORMATTING

* Output ONLY valid Enforce Script inside standard code blocks (````enforce` or ````cpp`).
* Do not invent methods that do not exist in the DayZ Script API. If unsure about a method signature, request verification via the MCP tools.

```

---

### 3. `schemas/dayz_mcp_tools_schema.json`
*(Guárdalo en una carpeta `schemas/`)*

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "DayZ MCP Tools Schema Specification",
  "description": "Especificación optimizada de herramientas MCP para DayZ/Enforce Script diseñada para minimizar fricción y alucinaciones en modelos de lenguaje pequeños.",
  "tools": [
    {
      "name": "dayz_search_api_symbol",
      "description": "Busca clases, métodos, eventos, constantes o variables globales en la API de Enforce Script de DayZ / Enfusion Engine. Devuelve firmas exactas, parámetros y ámbito (Game / GUI / World). Usar SIEMPRE antes de sugerir llamadas a métodos desconocidos.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Nombre exacto o parcial de la clase, función o constante a buscar (ej: 'EntityAI', 'OnStoreSave', 'RPC_GAME').",
            "minLength": 2
          },
          "symbol_type": {
            "type": "string",
            "description": "Filtrar por tipo de símbolo para acotar resultados en el contexto del modelo.",
            "enum": ["class", "method", "variable", "enum", "event", "all"],
            "default": "all"
          },
          "module": {
            "type": "string",
            "description": "Módulo del motor/juego donde buscar.",
            "enum": ["1_Core", "2_GameLib", "3_Game", "4_World", "5_Mission", "all"],
            "default": "all"
          },
          "exact_match": {
            "type": "boolean",
            "description": "Si es True, solo devuelve coincidencias exactas. Recomendado para modelos pequeños para reducir tokens de respuesta.",
            "default": false
          }
        },
        "required": ["query"],
        "additionalProperties": false
      }
    },
    {
      "name": "dayz_get_class_details",
      "description": "Obtiene la jerarquía de herencia completa, métodos heredados, variables miembro y eventos de una clase de Enforce Script específica. Indispensable para verificar hooks disponibles (ej. en 'PlayerBase', 'ItemBase', 'BuildingSuper').",
      "inputSchema": {
        "type": "object",
        "properties": {
          "class_name": {
            "type": "string",
            "description": "Nombre exacto de la clase Enforce Script (ej: 'ItemBase', 'PlayerBase', 'InventoryItem')."
          },
          "include_inherited": {
            "type": "boolean",
            "description": "Incluir métodos y miembros heredados de clases padre.",
            "default": true
          },
          "include_source_file": {
            "type": "boolean",
            "description": "Devolver la ruta del archivo script de origen en el juego base para referencia.",
            "default": false
          }
        },
        "required": ["class_name"],
        "additionalProperties": false
      }
    },
    {
      "name": "dayz_validate_enforcescript",
      "description": "Valida un fragmento de código Enforce Script contra las reglas del compilador de DayZ. Detecta errores comunes como alucinación de métodos de C++, uso incorrecto de 'ref', tipado dinámico no soportado o falta de modding hooks (modded class).",
      "inputSchema": {
        "type": "object",
        "properties": {
          "code": {
            "type": "string",
            "description": "El bloque de código Enforce Script a validar."
          },
          "script_module": {
            "type": "string",
            "description": "Capa del módulo del script para validar accesibilidad de tipos.",
            "enum": ["3_Game", "4_World", "5_Mission"],
            "default": "4_World"
          },
          "check_memory_leaks": {
            "type": "boolean",
            "description": "Verificar el uso correcto del keyword 'ref' y destrucción de punteros para prevenir fugas de memoria en Enfusion.",
            "default": true
          }
        },
        "required": ["code"],
        "additionalProperties": false
      }
    },
    {
      "name": "dayz_get_modding_hook_template",
      "description": "Genera la plantilla o estructura estándar para sobrescribir o extender (modded class) un comportamiento o método en DayZ, incluyendo llamada a super, comprobaciones de null y sincronización Servidor/Cliente.",
      "inputSchema": {
        "type": "object",
        "properties": {
          "target_class": {
            "type": "string",
            "description": "Clase base que se desea modear (ej: 'PlayerBase', 'DayZGame', 'ItemBase')."
          },
          "hook_type": {
            "type": "string",
            "description": "Tipo de evento o hook a implementar.",
            "enum": [
              "OnStoreSave_OnStoreLoad",
              "EEInit",
              "OnRPC",
              "SetActions",
              "OnWorkStart_OnWorkStop",
              "Custom_Method_Override"
            ]
          },
          "context": {
            "type": "string",
            "description": "Entorno de ejecución donde correrá el hook.",
            "enum": ["ServerOnly", "ClientOnly", "Shared_RPC", "Both_Side"],
            "default": "Both_Side"
          }
        },
        "required": ["target_class", "hook_type"],
        "additionalProperties": false
      }
    }
  ]
}

```

---

### 4. `README.md`

*(Guárdalo en la raíz de tu repositorio)*

```markdown
# dayz-mcp Optimization Pack (AI Native)

Este paquete contiene la configuración y esquemas optimizados para hacer que el servidor **dayz-mcp** sea consumible de forma directa y sin fricción por modelos de lenguaje (LLMs grandes, Flash o modelos pequeños/locales).

## Estructura de Archivos AI Native

- **`llms.txt`**: Índice estándar para la raíz del repositorio. Permite a las IAs entender el propósito del MCP, sus capacidades y las herramientas disponibles en un solo vistazo.
- **`system_prompt.md`**: Prompt del sistema diseñado para evitar que la IA alucine sintaxis de C++ / STL y fuerce el uso correcto de Enforce Script (manejo de `ref`, `modded class`, herencia).
- **`schemas/dayz_mcp_tools_schema.json`**: Especificación JSON Schema estricta para las herramientas MCP. Incluye `additionalProperties: false` y enums cerrados para reducir llamadas fallidas de function calling en modelos pequeños.

## Guía de Instalación

1. Coloca `llms.txt` y `system_prompt.md` en la raíz de tu repositorio.
2. Guarda el esquema dentro de `schemas/dayz_mcp_tools_schema.json`.
3. Importa las definiciones de `dayz_mcp_tools_schema.json` dentro de tu servidor MCP para declarar la lista de herramientas disponibles al cliente.

```