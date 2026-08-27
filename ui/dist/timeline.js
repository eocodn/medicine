function medicationCourseHtml(course) {
    if (!course)
        return "";
    let statusText;
    if (course.status === "upcoming") {
        statusText = "시작 전";
    }
    else if (course.status === "completed") {
        statusText = "복용기간 종료";
    }
    else {
        statusText = `${course.current_day}일째 · ${course.remaining_days}일 남음`;
    }
    const percent = Math.max(0, Math.min(100, Number(course.progress_percent) || 0));
    return `
    <div class="course-progress">
      <div><strong>전체 ${course.total_days}일</strong><span>${statusText}</span></div>
      <progress class="course-progress-track" value="${percent}" max="100" aria-label="복용 진행률 ${percent}%">${percent}%</progress>
    </div>`;
}
