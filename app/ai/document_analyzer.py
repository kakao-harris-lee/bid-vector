"""Document analysis AI module"""
import re
from typing import Dict, List


def analyze_document(content: str, document_type: str = "specification") -> Dict:
    """
    Analyze project document and extract key information.

    Args:
        content: Document content text
        document_type: Type of document (requirement, specification, etc.)

    Returns:
        Dictionary with analysis results
    """
    # Extract key requirements (simplified - use NLP for production)
    requirements = extract_requirements(content)

    # Calculate complexity score
    complexity = calculate_complexity(content, requirements)

    # Estimate effort
    effort = estimate_effort(content, requirements, complexity)

    # Identify potential risks
    risks = identify_risks(content, requirements)

    return {
        "key_requirements": requirements,
        "complexity_score": round(complexity, 2),  # 0-1 scale
        "estimated_effort": round(effort, 2),  # Effort score
        "risks": risks,
    }


def extract_requirements(content: str) -> List[str]:
    """Extract key requirements from document"""
    # Simplified extraction - look for common patterns
    requirements = []

    # Look for numbered requirements
    numbered_pattern = r'\d+\.\s*([^.\n]+)'
    matches = re.findall(numbered_pattern, content)
    requirements.extend(matches[:5])

    # Look for bullet points
    bullet_pattern = r'[-•]\s*([^.\n]+)'
    matches = re.findall(bullet_pattern, content)
    requirements.extend(matches[:5])

    # Look for "must", "should", "required" keywords
    keyword_pattern = r'(?:must|should|required|shall|needs?)\s+([^.\n,;]{10,100})'
    matches = re.findall(keyword_pattern, content, re.IGNORECASE)
    requirements.extend(matches[:5])

    # Remove duplicates and limit
    unique_requirements = list(set(req.strip() for req in requirements))
    return unique_requirements[:10]


def calculate_complexity(content: str, requirements: List[str]) -> float:
    """Calculate document/project complexity"""
    # Factors:
    # 1. Document length
    # 2. Number of requirements
    # 3. Technical keywords

    complexity = 0.0

    # Length factor (0-0.3)
    doc_length = len(content)
    length_factor = min(doc_length / 10000, 0.3)
    complexity += length_factor

    # Requirements factor (0-0.3)
    req_factor = min(len(requirements) / 20, 0.3)
    complexity += req_factor

    # Technical keywords factor (0-0.4)
    tech_keywords = [
        "architecture", "integration", "api", "database", "security",
        "scalability", "performance", "testing", "deployment",
        "infrastructure", "cloud", "microservices", "kubernetes"
    ]
    keyword_count = sum(1 for keyword in tech_keywords if keyword in content.lower())
    tech_factor = min(keyword_count / 15, 0.4)
    complexity += tech_factor

    return min(complexity, 1.0)


def estimate_effort(content: str, requirements: List[str], complexity: float) -> float:
    """Estimate effort required (relative score)"""
    # Base effort on:
    # 1. Number of requirements
    # 2. Complexity
    # 3. Document detail level

    effort = 0.0

    # Requirements impact (base effort)
    effort += len(requirements) * 0.1

    # Complexity impact (0-1.0)
    effort += complexity * 1.0

    # Detail level impact
    avg_req_length = sum(len(req) for req in requirements) / len(requirements) if requirements else 0
    if avg_req_length > 100:
        effort += 0.5
    elif avg_req_length > 50:
        effort += 0.25

    return min(effort, 5.0)  # Cap at 5.0


def identify_risks(content: str, requirements: List[str]) -> List[str]:
    """Identify potential risks in the project"""
    risks = []

    # Risk patterns
    risk_keywords = {
        "tight deadline": ["urgent", "immediate", "asap", "deadline"],
        "high budget": ["expensive", "costly", "high budget", "million"],
        "unclear requirements": ["unclear", "vague", "not specified", "tbd"],
        "complex integration": ["integration", "api", "third-party", "external"],
        "security concerns": ["security", "sensitive", "confidential", "encrypted"],
        "scalability needs": ["scale", "high volume", "many users", "growth"],
        "compliance required": ["compliance", "regulatory", "gdpr", "hipaa"],
    }

    for risk_name, keywords in risk_keywords.items():
        if any(keyword in content.lower() for keyword in keywords):
            risks.append(risk_name)

    # Complexity-based risk
    if len(requirements) > 10:
        risks.append("high number of requirements")

    return risks[:5]  # Limit to top 5 risks
