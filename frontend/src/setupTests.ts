import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// 다수 화면이 React.lazy 코드 스플릿이라 전체 스위트 동시 실행 시 청크 로드가
// RTL 기본 1s asyncUtilTimeout을 넘겨 findBy/waitFor가 간헐적으로 실패한다.
// 타임아웃만 늘릴 뿐 단언 자체는 그대로 유지한다(요소 존재를 똑같이 검증).
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
});
