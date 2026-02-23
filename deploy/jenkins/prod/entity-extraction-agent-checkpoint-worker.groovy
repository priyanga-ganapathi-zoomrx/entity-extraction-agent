@Library('congress-ci-cd@main') _

ferma_congress_prod.call(
  repo_url: "https://github.com/ZoomRx/entity-extraction-agent.git",
  deployment_type: "backend",
  application_name: "entity-extraction-agent-checkpoint-worker",
  chart_version: "v3.1.0",
  mode_parameters: "deploy,rollback,restart",
  dockerfile_dir: "deploy/entity-extraction-agent/",
  chart_name: "application_chart",
  image_name: "entity-extraction-agent"
)
