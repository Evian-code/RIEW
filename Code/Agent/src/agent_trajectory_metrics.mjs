function successfulLinkSteps(steps) {
  return (steps || []).filter(step => step.action === 'follow_link' && step.tool_result_status === 'ok');
}

export function deriveRestrictedLinkAttemptMetrics({ steps }) {
  if (!Array.isArray(steps)) throw new Error('steps must be an array');
  const attempts = steps
    .filter(step => step.action === 'follow_link'
      && step.tool_result_status === 'error'
      && step.tool_result_error === 'link_not_available_in_current_interface')
    .map(step => Object.freeze({
      step: step.step_index,
      current_url: step.current_url,
      requested_link_id: step.selected_link_id,
      requested_target_url: step.destination_url,
      error_type: step.tool_result_error
    }));
  return Object.freeze({
    restricted_link_attempt_count: attempts.length,
    restricted_link_attempts: Object.freeze(attempts)
  });
}

export function deriveTrajectoryNavigationMetrics({ steps, visitedUrls, targetUrl }) {
  if (!Array.isArray(steps)) throw new Error('steps must be an array');
  if (!Array.isArray(visitedUrls)) throw new Error('visitedUrls must be an array');
  if (typeof targetUrl !== 'string' || targetUrl.length === 0) throw new Error('targetUrl is required');

  const links = successfulLinkSteps(steps);
  const targetIsStart = visitedUrls.length > 0 && visitedUrls[0] === targetUrl;
  const firstTargetLinkIndex = links.findIndex(step => step.destination_url === targetUrl);
  const firstTargetStep = firstTargetLinkIndex >= 0 ? links[firstTargetLinkIndex] : null;
  const uniquePages = new Set(visitedUrls).size;

  return Object.freeze({
    first_target_reached_step: targetIsStart
      ? 0
      : firstTargetStep
        ? (Number.isInteger(firstTargetStep.step_index) ? firstTargetStep.step_index : steps.indexOf(firstTargetStep))
        : null,
    steps_to_first_target: targetIsStart ? 0 : firstTargetLinkIndex >= 0 ? firstTargetLinkIndex + 1 : null,
    total_link_steps: links.length,
    visited_url_sequence: Object.freeze([...visitedUrls]),
    unique_pages: uniquePages,
    revisit_count: visitedUrls.length - uniquePages
  });
}
