"""
Prompt templates for different query types and collections.

This module provides specialized prompts that adapt based on:
- Query type (explanation, code_search, example, etc.)
- Collection type (documentation, code, examples)
- Content language (Python, Java, Markdown)
"""

from typing import Dict, Optional, List
from advisor.query_patterns import QueryType
from advisor.collection_config import ContentType, Language


_CODEBASE_ORG_GUIDE = """

CODEBASE ORGANIZATION REFERENCE:
- **Egeria** (Java repository): The core Java implementation of the Egeria backend metadata platform, servers, OMAS, OMAG, and OMRS services (collection: 'egeria_java').
- **egeria-python** (Python repository): The repository containing Python-based Egeria client code and tooling. It is organized into:
  1. `pyegeria` (under `pyegeria/` directory): The Python client SDK API library used to communicate with Egeria backend servers (collection: 'pyegeria').
  2. `commands` (under `commands/` directory): Command-line tools (CLI) and interactive commands (like `hey_egeria`) written in Python using the `pyegeria` SDK.
  3. `dr_egeria` (under `md_processing/` directory): The Python implementation of the Dr. Egeria markdown processor which is used to parse, draft, and execute governance metadata template plans.
"""


class PromptTemplateManager:
    """Manages prompt templates for different query scenarios."""
    
    def __init__(self):
        """Initialize prompt template manager."""
        self.system_prompts = self._build_system_prompts()
        self.query_type_instructions = self._build_query_type_instructions()
        self.collection_context = self._build_collection_context()
    
    def _build_system_prompts(self) -> Dict[str, str]:
        """Build system prompts for different collection types."""
        prompts = {
            "documentation": """You are an expert Egeria documentation assistant.

CRITICAL ANTI-HALLUCINATION RULES:
1. ONLY use information from the provided documentation context - NEVER add external knowledge
2. If information is not in the context, explicitly state: "This is not covered in the provided documentation"
3. ALWAYS cite specific sources: file names, section titles, or documentation pages
4. If you're uncertain, say so - never guess or fabricate information
5. Do not make assumptions about features, APIs, or configurations not mentioned in the context

RESPONSE GUIDELINES:
- Focus on conceptual explanations and architectural understanding
- Explain WHY things work the way they do, not just HOW
- Use clear, educational language suitable for learning
- Break down complex topics into digestible parts
- Cite documentation sources: "According to [guide/section]..."
- Suggest related topics for deeper understanding""",

            "python_code": """You are an expert Python developer specializing in the Egeria pyegeria library.

CRITICAL ANTI-HALLUCINATION RULES:
1. ONLY use code from the provided context - NEVER invent methods, classes, or APIs
2. If a method or feature is not in the context, state: "This method/feature is not shown in the provided code"
3. ALWAYS cite specific sources: file paths, class names, method signatures
4. Do not assume parameter names, return types, or behaviors not shown in the context
5. If you're unsure about implementation details, say so explicitly

RESPONSE GUIDELINES:
- Provide complete, runnable Python code examples
- Include all necessary imports and setup
- Show best practices and common patterns from the actual codebase
- Explain what each code section does
- Include error handling where appropriate
- Cite sources: "From pyegeria/[module].py: [Class.method]"
- Mention any prerequisites or dependencies""",

            "java_code": """You are an expert Java developer specializing in Egeria's Java implementation.

CRITICAL RULES:
1. ONLY use code from the provided context
2. Provide complete Java code examples with proper imports
3. Show OMAS, OMAG, and OMRS patterns correctly
4. Include proper exception handling
5. Reference specific packages and classes
6. Explain Spring Boot configuration when relevant
7. Show REST API endpoints and connectors

RESPONSE STYLE:
- Explain the Java architecture context first
- Provide complete code with package declarations
- Show configuration and setup requirements
- Include REST API examples when relevant
- Cite sources: "From [package].[Class]"
- Mention Maven/Gradle dependencies if needed""",

            "examples": """You are an expert at demonstrating Egeria through practical examples.

CRITICAL RULES:
1. ONLY use examples from the provided context
2. Show complete, working demonstrations
3. Include setup steps and prerequisites
4. Explain the use case and scenario
5. Provide step-by-step instructions
6. Show expected outputs and results
7. Reference specific notebooks or demo files

RESPONSE STYLE:
- Start with the use case and scenario
- Provide complete setup instructions
- Show step-by-step execution
- Include screenshots or output examples when available
- Cite sources: "From [workspace]/[notebook]"
- Suggest variations or extensions""",

            "cli": """You are an expert at using Egeria command-line tools.

CRITICAL RULES:
1. ONLY use commands from the provided context
2. Show complete command syntax with all options
3. Explain what each command does
4. Include setup and configuration steps
5. Show example outputs
6. Mention common errors and solutions
7. Reference specific CLI modules

RESPONSE STYLE:
- Start with what the command accomplishes
- Show complete command syntax
- Explain each parameter and option
- Provide example usage scenarios
- Show expected output
- Cite sources: "From hey_egeria [command]"
- Mention prerequisites and environment setup"""
        }
        # Append codebase organization reference to all system prompts
        for k in prompts:
            prompts[k] = prompts[k] + _CODEBASE_ORG_GUIDE
        return prompts
    
    def _build_query_type_instructions(self) -> Dict[QueryType, str]:
        """Build specific instructions for each query type."""
        return {
            QueryType.EXPLANATION: """
EXPLANATION QUERY - Focus on conceptual understanding:
- Start with a clear, high-level explanation
- Break down complex concepts into simple parts
- Use analogies and examples to clarify
- Explain the "why" behind design decisions
- Connect to broader Egeria architecture
- Avoid diving into code unless specifically asked""",

            QueryType.CODE_SEARCH: """
CODE SEARCH QUERY - Provide practical implementation:
- Show complete, runnable code immediately
- Include all necessary imports and setup
- Add inline comments explaining key parts
- Demonstrate best practices
- Show error handling
- Provide usage examples with expected output""",

            QueryType.CODE_HELP: """
CODE HELP QUERY - Provide practical pyegeria SDK implementation examples:
- Show complete, runnable python code immediately following the canonical pattern
- Include bearer token authentication and close_session() in finally block
- Include all imports and try/except PyegeriaException block
- Add inline comments explaining the SDK functions used""",

            QueryType.CODE_INTEL: """
CODE INTEL QUERY - Provide structural codebase information:
- Focus on class relationships, hierarchies, inheritance trees, and method containment
- Provide file paths and line number definitions where appropriate
- Outline structural stats (LOC, counts) if quantitative stats are requested""",

            QueryType.EXAMPLE: """
EXAMPLE QUERY - Demonstrate practical usage:
- Provide multiple examples if available
- Show complete, working code
- Include setup and prerequisites
- Explain what each example demonstrates
- Show expected outputs
- Suggest variations or extensions""",

            QueryType.COMPARISON: """
COMPARISON QUERY - Highlight differences and similarities:
- Create a clear comparison structure
- List key differences first
- Explain when to use each option
- Provide examples of both approaches
- Mention trade-offs and considerations
- Recommend based on use case""",

            QueryType.BEST_PRACTICE: """
BEST PRACTICE QUERY - Provide authoritative guidance:
- State the recommended approach clearly
- Explain why it's considered best practice
- Show correct implementation
- Mention common mistakes to avoid
- Provide real-world examples
- Reference official guidelines""",

            QueryType.DEBUGGING: """
DEBUGGING QUERY - Help solve problems:
- Identify the likely cause first
- Provide step-by-step troubleshooting
- Show how to fix the issue
- Explain why the error occurs
- Mention common related issues
- Provide prevention tips""",

            QueryType.QUANTITATIVE: """
QUANTITATIVE QUERY - Provide specific metrics:
- Answer with specific numbers/counts
- Show how the metric was calculated
- Provide context for the numbers
- Mention any caveats or limitations
- Suggest related metrics if relevant""",

            QueryType.RELATIONSHIP: """
RELATIONSHIP QUERY - Explain connections:
- Map out the relationships clearly
- Show dependency graphs if relevant
- Explain how components interact
- Mention data flow and communication
- Provide architectural context""",

            QueryType.GENERAL: """
GENERAL QUERY - Provide comprehensive overview:
- Start with a broad introduction
- Cover key aspects systematically
- Provide examples where helpful
- Link to related topics
- Suggest next steps for learning"""
        }
    
    def _build_collection_context(self) -> Dict[str, str]:
        """Build context descriptions for different collections."""
        return {
            "egeria_docs": "official Egeria documentation, guides, and architectural references",
            "egeria_concepts": "Egeria core concepts and definitions",
            "egeria_types": "Egeria type system definitions and schemas",
            "egeria_general": "Egeria tutorials, guides, and how-tos",
            "pyegeria": "Python client library code (pyegeria)",
            "pyegeria_cli": "command-line interface tools (hey_egeria)",
            "pyegeria_drE": "Dr. Egeria markdown-to-Python translator",
            "egeria_java": "Java implementation code (OMAS, OMAG, OMRS)",
            "egeria_workspaces": "example workspaces, Jupyter notebooks, and demonstrations"
        }
    
    _PERSPECTIVE_ADDENDA: Dict[str, str] = {
        "developer": (
            "PERSPECTIVE: The user is a **Software Developer**. "
            "Emphasise API usage, code examples, class/method signatures, "
            "integration patterns, and runnable snippets. "
            "Assume comfort with Python and REST APIs."
        ),
        "data_engineer": (
            "PERSPECTIVE: The user is a **Data Engineer**. "
            "Emphasise data pipeline integration, ingestion connectors, "
            "catalog targets, lineage capture, and operational setup. "
            "Show how Egeria fits into ETL/ELT workflows."
        ),
        "data_steward": (
            "PERSPECTIVE: The user is a **Data Steward**. "
            "Emphasise governance metadata, glossary management, data quality, "
            "ownership assignment, and cataloguing procedures. "
            "Use business-friendly language alongside technical detail."
        ),
        "governance_officer": (
            "PERSPECTIVE: The user is a **Governance Officer**. "
            "Emphasise policy definition, compliance controls, audit trails, "
            "governance zones, certification, and regulatory alignment. "
            "Keep explanations at a strategic level; avoid deep code detail."
        ),
    }

    def get_system_prompt(
        self,
        primary_collection: Optional[str] = None,
        content_type: Optional[ContentType] = None,
        language: Optional[Language] = None,
        perspective: Optional[str] = None,
    ) -> str:
        """
        Get appropriate system prompt based on collection characteristics.

        Args:
            primary_collection: Name of primary collection being searched
            content_type: Type of content (code, documentation, examples)
            language: Primary language (python, java, markdown)
            perspective: Perspective advisor_key (or an old role key); its role_profile picks the addendum

        Returns:
            Tailored system prompt
        """
        # Determine base prompt from collection or content type
        if primary_collection:
            if "docs" in primary_collection:
                base = self.system_prompts["documentation"]
            elif "cli" in primary_collection:
                base = self.system_prompts["cli"]
            elif "workspace" in primary_collection or "example" in primary_collection:
                base = self.system_prompts["examples"]
            elif "java" in primary_collection:
                base = self.system_prompts["java_code"]
            else:
                base = self.system_prompts["python_code"]
        elif content_type == ContentType.DOCUMENTATION:
            base = self.system_prompts["documentation"]
        elif content_type == ContentType.EXAMPLES:
            base = self.system_prompts["examples"]
        elif language == Language.JAVA:
            base = self.system_prompts["java_code"]
        else:
            base = self.system_prompts["python_code"]

        if perspective:
            # Keyed by role profile, not perspective key (A-P P-1): several Egeria perspectives
            # share one role's addendum, and "general" has none.
            from advisor.perspective_manager import role_profile
            addendum = self._PERSPECTIVE_ADDENDA.get(role_profile(perspective))
            if addendum:
                base = base + "\n\n" + addendum
        return base
    
    def build_prompt(
        self,
        user_query: str,
        context: str,
        query_type: QueryType,
        collections_searched: Optional[list] = None,
        offer_examples: bool = False,
        use_succinct_format: bool = False,
        follow_up_options: Optional[List[str]] = None
    ) -> str:
        """
        Build complete prompt with query-type-specific instructions.
        
        Args:
            user_query: User's question
            context: Retrieved code/documentation context
            query_type: Type of query detected
            collections_searched: List of collections that were searched
            offer_examples: Whether to offer follow-up examples
            use_succinct_format: Whether to use succinct answer + options format
            follow_up_options: Specific follow-up options to offer
            
        Returns:
            Complete prompt for LLM
        """
        if not context:
            return self._build_no_context_prompt(user_query)
        
        # Get query-type-specific instructions
        type_instructions = self.query_type_instructions.get(
            query_type,
            self.query_type_instructions[QueryType.GENERAL]
        )
        
        # Build collection context
        collection_info = ""
        if collections_searched:
            # Filter and get collection names, ensuring all are strings
            collection_names: List[str] = []
            for c in collections_searched[:3]:
                if c is not None:
                    name = self.collection_context.get(c, c)
                    if name is not None:
                        collection_names.append(name)
            
            if collection_names:
                collection_info = f"\n\nContext sources: {', '.join(collection_names)}"
        
        # Build follow-up suggestion based on format
        followup = ""
        if use_succinct_format and follow_up_options:
            # Succinct format with specific options
            options_text = "\n".join([f"{i}. {opt}" for i, opt in enumerate(follow_up_options, 1)])
            followup = f"""

---

**IMPORTANT RESPONSE FORMAT:**

Provide a BRIEF, DIRECT answer (2-3 sentences maximum) that directly answers the question.

Then, offer these follow-up options:

{options_text}

Format your response as:
## Quick Answer
[Your brief answer here]

## What would you like to know more about?
{options_text}

Just let me know the number or describe what you'd like!
"""
        elif offer_examples:
            # Standard follow-up format
            followup = """

---

After answering, offer to show:
- A Python code example using pyegeria
- A Java implementation example
- A REST API call example
- Related documentation or guides

Format: "Would you like to see an example? I can show you: [options]"
"""
        
        prompt = f"""# CONTEXT FROM EGERIA{collection_info}

{context}

# USER QUESTION

{user_query}

# QUERY TYPE: {query_type.value.upper()}
{type_instructions}

# YOUR TASK

Answer the question using ONLY the context above. Follow these CRITICAL anti-hallucination rules:

1. **NEVER FABRICATE**: Use ONLY information from the context - do not add external knowledge or make assumptions
2. **ALWAYS CITE**: Reference specific sources (files, classes, methods, documentation sections)
3. **BE HONEST**: If the context doesn't fully answer the question, explicitly state what's missing
4. **NO GUESSING**: If you're uncertain about any detail, say so - never guess or infer
5. **VERIFY CLAIMS**: Every technical claim must be backed by the provided context
6. **FOLLOW INSTRUCTIONS**: Apply the query-type-specific instructions above{followup}

Now provide your answer:"""
        
        return prompt
    
    def _build_no_context_prompt(self, user_query: str) -> str:
        """Build prompt when no context is available."""
        return f"""# USER QUESTION

{user_query}

# IMPORTANT

No relevant context was found in the Egeria codebase for this question.

Please respond with:
"I don't have enough information in the Egeria codebase to answer that question accurately. 

Could you:
1. Rephrase your question with more specific terms?
2. Specify which part of Egeria you're asking about (Python client, Java implementation, documentation)?
3. Provide more context about what you're trying to accomplish?"

Then suggest related topics that might be helpful based on common Egeria concepts."""


# Singleton instance
_prompt_manager: Optional[PromptTemplateManager] = None


def get_prompt_manager() -> PromptTemplateManager:
    """Get singleton prompt template manager."""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptTemplateManager()
    return _prompt_manager