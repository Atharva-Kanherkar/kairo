use dynamo_parsers::tool_calling::parsers::{detect_and_parse_tool_call, detect_and_parse_tool_call_with_recovery};
#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    for path in std::env::args().skip(1) {
        let raw = std::fs::read_to_string(&path)?;
        for (name, plain) in [("plain", true), ("recovery", false)] {
            let (calls, normal) = if plain {
                detect_and_parse_tool_call(&raw, Some("hermes"), None).await?
            } else {
                detect_and_parse_tool_call_with_recovery(&raw, Some("hermes"), None).await?
            };
            let args: Vec<_> = calls.iter().map(|c| format!("{}:{}", c.function.name, c.function.arguments)).collect();
            println!("{path} [{name}] tool_calls={} normal_text_len={} args={:?}", calls.len(), normal.as_deref().map(str::len).unwrap_or(0), args);
        }
    }
    Ok(())
}
