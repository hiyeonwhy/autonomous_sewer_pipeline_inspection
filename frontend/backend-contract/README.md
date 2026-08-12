# React–Spring Boot 데이터 계약

이 폴더는 React 코드를 Java로 변환하기 위한 것이 아니라, React와 Spring Boot가
JSON API로 통신할 때 사용할 데이터 계약을 정의한다.

## 구성

```text
backend-contract/
├─ java/
│  ├─ dto/
│  └─ enums/
├─ ENUMS.md
└─ API_EXAMPLES.md
```

Java 예제의 기본 패키지는 `com.ssafy.robotinspection.api`로 작성했다. 실제
Spring Boot 프로젝트 패키지에 맞게 변경한다.

## 공통 원칙

- 서버 ID는 `Long`, 프론트에서는 `number`로 사용한다.
- Java 필드는 `camelCase`, JSON 필드는 기존 API와 동일한 `snake_case`를 사용한다.
- 날짜와 시간은 ISO 8601 형식으로 전달한다.
- 상태값은 한글이 아닌 영문 Enum 코드로 저장한다.
- 한글 문구는 프론트에서 Enum 코드에 맞춰 표시한다.
- `confidence`는 AI 신뢰도이며 결함 등급과 별도로 관리한다.
- 목록 응답은 우선 단순 배열로 통일한다.

Spring Boot에서 snake_case JSON을 사용하려면 다음 설정을 적용한다.

```yaml
spring:
  jackson:
    property-naming-strategy: SNAKE_CASE
```

Spring 담당자가 Swagger를 제공하면 `src/types/api.ts`, `src/api/*`,
`src/api/adapters.ts`를 최종 Swagger에 맞게 갱신한다.

