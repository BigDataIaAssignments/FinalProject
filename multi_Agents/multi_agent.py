from typing import TypedDict, Optional, List, Dict
from langgraph.graph import StateGraph, END
import asyncio
from datetime import datetime
import json
from multi_Agents.websearch_agent import WebSearchRecommender
from multi_Agents.gate_agent import CollegeRecommender
from dotenv import load_dotenv
from multi_Agents.validate_recommender import validate_and_compare

load_dotenv()

class RecommendationState(TypedDict):
    user_query: str
    is_college_related: bool
    is_comparison_query: bool  # New field
    safety_check_passed: bool
    combined_agent_results: Optional[str]
    snowflake_results: List[Dict]
    rag_results: List[Dict]
    web_results: List[Dict]
    final_output: Optional[Dict]
    early_response: Optional[str]
    fallback_used: Optional[bool]
    fallback_message: Optional[str]

workflow = StateGraph(RecommendationState)

college_recommender = CollegeRecommender()

async def detect_comparison_node(state: RecommendationState):
    """New node to detect comparison queries"""
    STANDARD_RESPONSE = "I specialize in college recommendations, not comparisons. Please ask about specific programs or colleges."
    
    # Simple keyword-based detection (you could replace with LLM-based detection)
    comparison_keywords = ["compare", "vs", "versus", "difference", "better", "worse", "ranking"]
    
    query_lower = state['user_query'].lower()
    is_comparison = any(keyword in query_lower for keyword in comparison_keywords)
    
    print(f"\n🔍 Comparison check for: '{state['user_query']}'")
    print(f"Comparison detected: {is_comparison}")
    
    return {
        "is_comparison_query": is_comparison,
        "early_response": STANDARD_RESPONSE if is_comparison else None
    }

async def check_prompt_node(state: RecommendationState):
    STANDARD_RESPONSE = "Sorry I can't do that. I can assist you with college recommendations."
    
    print(f"\n🔍 Processing query: '{state['user_query']}'")
    
    # Skip classification if already identified as comparison
    if state.get('is_comparison_query', False):
        return {
            "is_college_related": False,
            "safety_check_passed": False,
            "early_response": state.get('early_response', STANDARD_RESPONSE)
        }
    
    classification = await college_recommender.check_and_classify_query(state['user_query'])
    
    print(f"📊 Classification results:")
    print(f"  - is_college_related: {classification['is_college_related']}")
    print(f"  - safety_check_passed: {classification['safety_check_passed']}")
    print(f"  - context: {classification['context']}")
    
    if classification["context"] != "college":
        print("❌ Query rejected (not college-related or failed safety check)")
        return {
            "is_college_related": False,
            "safety_check_passed": classification["safety_check_passed"],
            "early_response": classification.get("response", STANDARD_RESPONSE)
        }
    
    print("✅ Query accepted as college-related")
    return {
        "is_college_related": True,
        "safety_check_passed": True
    }

#output from our rag and snowflake agents
async def query_combined_agent_node(state: RecommendationState):
    '''
    """TEST VERSION - Always returns empty results to trigger fallback"""
    print("\n🔍 TEST MODE: Combined agent returning empty results to trigger fallback")
    
    return {
        **state,  # Preserve existing state
        "combined_agent_results": None,
        "snowflake_results": [],  # Empty list to trigger fallback
        "rag_results": [],  # Empty list to trigger fallback
        "fallback_used": False  # Not yet used, but will be triggered by check_results_node
    }
'''
    try:
        result = validate_and_compare(state['user_query'])
        
        print("\n🔍 Raw results from validate_and_compare:")
        print(f"Combined output length: {len(result.get('combined_agent_results', ''))}")
        print(f"Snowflake results type: {type(result.get('snowflake_results'))} count: {len(result.get('snowflake_results', []))}")
        print(f"RAG results type: {type(result.get('rag_results'))} count: {len(result.get('rag_results', []))}")

        return {
            **state,  
            "combined_agent_results": result["combined_agent_results"],
            "snowflake_results": result.get("snowflake_results", []),
            "rag_results": result.get("rag_results", []),
            "fallback_used": False
        }
    except Exception as e:
        print(f"❌ Combined agent error: {e}")
        return {
            **state,
            "combined_agent_results": None,
            "snowflake_results": [],
            "rag_results": [],
            "fallback_used": True,
            "fallback_message": "Error processing your request"
        }

#checking output for fallback trigger
async def check_results_node(state: RecommendationState):
    """Check if we should fall back to web search"""
    no_data = (not state.get('snowflake_results') and 
               not state.get('rag_results'))
    

    combined_empty = (state.get('combined_agent_results') and 
                     "❌ No valid data found" in state['combined_agent_results'])
    
    if no_data or combined_empty:
        print("⚠️ Both Snowflake and RAG returned empty results")
        return {"should_fallback": True}
    
    return {"should_fallback": False}

async def query_web_node(state: RecommendationState):
    """Process query with existing Web Search agent"""
    try:
       
        recommender = WebSearchRecommender()
        
       
        result = await recommender.recommend(state['user_query'])
        
        # Format the results to match our multi-agent structure
        formatted_results = [{
            'text': result['response'],
            'metadata': {
                'source': 'web_search_fallback',
                'results_analyzed': result['results_analyzed']
            }
        }]
        
        print("\n🌐 Web Search Raw Output (Fallback):")
        print(json.dumps(formatted_results, indent=2))
        return {
            "web_results": formatted_results,
            "fallback_used": True,
            "fallback_message": "We're using web search results as a fallback since we couldn't find relevant information in our databases."
        }
    except Exception as e:
        print(f"❌ Web Search error: {e}")
        return {
            "web_results": [],
            "fallback_used": False
        }

#compiling all the results
def compile_results(state: RecommendationState):
    output = {
        "query": state['user_query'],
        "combined_output": state.get('combined_agent_results'),
        "snowflake": state.get('snowflake_results', []),
        "rag": state.get('rag_results', [])
    }
    
    if state.get('fallback_used', False):
        output.update({
            "web": state.get('web_results', []),
            "fallback_used": True,
            "fallback_message": state.get('fallback_message', '')
        })
    else:
        output.update({
            "web": [],
            "fallback_used": False
        })
        
    return {"final_output": output}





# Modified workflow construction
workflow.add_node("detect_comparison", detect_comparison_node)
workflow.add_node("gatekeeper", check_prompt_node)
workflow.add_node("combined_agent", query_combined_agent_node)
workflow.add_node("check_results", check_results_node)
workflow.add_node("web", query_web_node)
workflow.add_node("compile", compile_results)

workflow.set_entry_point("detect_comparison")

# First decision point - is this a comparison?
workflow.add_conditional_edges(
    "detect_comparison",
    lambda state: "early_exit" if state.get("is_comparison_query", False) else "gatekeeper",
    {
        "early_exit": END,
        "gatekeeper": "gatekeeper"
    }
)

# Original flow continues
workflow.add_conditional_edges(
    "gatekeeper",
    lambda state: (
        "early_exit" 
        if not state["is_college_related"] or not state["safety_check_passed"] 
        else "combined_agent"
    ),
    {
        "early_exit": END,
        "combined_agent": "combined_agent"
    }
)

workflow.add_edge("combined_agent", "check_results")
workflow.add_conditional_edges(
    "check_results",
    lambda state: "web" if state.get("should_fallback", False) else "compile",
)
workflow.add_edge("web", "compile")
workflow.add_edge("compile", END)

# Compile the graph
app = workflow.compile()

async def test_workflow(query: str):
    print(f"\n🔍 Testing query: '{query}'")
    initial_state = {
        "user_query": query,
        "is_college_related": False,
        "safety_check_passed": False,
        "combined_agent_results": None,
        "snowflake_results": [],  
        "rag_results": [],  
        "web_results": [],
        "final_output": None,
        "early_response": None,
        "fallback_used": False,
        "fallback_message": None
    }
    
    result = await app.ainvoke(initial_state)
    
    print("\n📊 Final State Inspection:")
    print(f"Final output keys: {result['final_output'].keys()}")
    print(f"Snowflake results sample: {result['final_output'].get('snowflake', [])[:1]}")
    print(f"RAG results sample: {result['final_output'].get('rag', [])[:1]}")

if __name__ == "__main__":
    test_queries = [
        "What MBA programs does Stanford offer for finance specialization?"
    ]
    
    for query in test_queries:
        print(f"\n{'='*50}\nTesting: '{query}'")
        result = asyncio.run(app.ainvoke({
            "user_query": query,
            "is_college_related": False,
            "safety_check_passed": False,
            "early_response": None,
            "combined_agent_results": None, 
            "web_results": [],
            "final_output": None,
            "fallback_used": False,
            "fallback_message": None
        }))
        
        if result.get("early_response"):
            print(f"RESPONSE: {result['early_response']}")
        else:
            print("PROCESSED COLLEGE QUERY")
            final_output = result.get('final_output', {})
            
            # Print fallback status if used
            if final_output.get('fallback_used'):
                print("\n⚠️ Fallback Web Search Used")
                print(f"Message: {final_output.get('fallback_message', '')}")
                
                # Print web results if available
                if final_output.get('web'):
                    print("\nWeb Search Results:")
                    for i, res in enumerate(final_output['web'], 1):
                        print(f"{i}. {res.get('text', '')[:200]}...")
            
            # Print combined results if available
            if final_output.get('combined_output'):
                print("\n🎯 COMBINED AGENT RESULTS:")
                print(final_output['combined_output'])