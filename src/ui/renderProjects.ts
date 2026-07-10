import type { Project } from '../types';

const resolveHref = (project: Project) => project.liveUrl || project.fallbackUrl;

export function renderProjects(projects: Project[], container: HTMLElement): void {
  container.innerHTML = '';
  for (const project of projects) {
    const anchor = document.createElement('a');
    anchor.className = 'planet-card';
    anchor.href = resolveHref(project);
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.dataset.tilt = '';
    anchor.dataset.projectMode = project.mode;
    anchor.dataset.projectId = project.id;
    anchor.dataset.deploymentStatus = project.deploymentStatus;
    anchor.setAttribute('aria-label', `打开 ${project.name} ${project.deploymentStatus} 入口`);

    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = project.category;

    const status = document.createElement('span');
    status.className = 'project-status';

    const compatibilityLevel = document.createElement('span');
    compatibilityLevel.className = 'compatibility-level';
    compatibilityLevel.textContent = project.compatibilityLevel;

    const deploymentStatus = document.createElement('span');
    deploymentStatus.className = 'deployment-status';
    deploymentStatus.textContent = project.deploymentStatus;

    const futureLevel = document.createElement('span');
    futureLevel.className = 'future-level';
    futureLevel.textContent = project.futureLevel;
    status.append(compatibilityLevel, deploymentStatus, futureLevel);

    const title = document.createElement('h3');
    title.textContent = project.name;

    const summary = document.createElement('p');
    summary.textContent = project.summary;

    const enter = document.createElement('span');
    enter.className = 'enter-copy';
    enter.textContent = project.liveUrl ? 'Visit verified surface' : 'Review source and deploy status';

    anchor.append(label, status, title, summary, enter);
    container.append(anchor);
  }
}
