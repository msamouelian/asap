MATCH (n)
WHERE n:NodeSchema OR n:RelationshipSchema OR n:PropertySchema
RETURN labels(n)[0] AS schema_type, properties(n) AS details
ORDER BY schema_type, details.label
