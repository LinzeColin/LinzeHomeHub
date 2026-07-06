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
    anchor.setAttribute('aria-label', `进入 ${project.name} 项目前端`);

    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = `${project.category} / ${project.status}`;

    const title = document.createElement('h3');
    title.textContent = project.name;

    const summary = document.createElement('p');
    summary.textContent = project.summary;

    const enter = document.createElement('span');
    enter.className = 'enter-copy';
    enter.textContent = 'Enter Project Frontend';

    anchor.append(label, title, summary, enter);
    container.append(anchor);
  }
}
