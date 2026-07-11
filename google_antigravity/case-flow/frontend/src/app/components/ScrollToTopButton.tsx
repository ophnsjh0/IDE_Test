'use client';

import { ActionIcon, Affix, Transition } from '@mantine/core';
import { useWindowScroll } from '@mantine/hooks';
import { IconArrowUp } from '@tabler/icons-react';

// 스크롤이 깊어지는 페이지 공용: 300px 이상 내리면 우측 하단에 맨 위로 버튼 표시
export default function ScrollToTopButton() {
  const [scroll, scrollTo] = useWindowScroll();

  return (
    <Affix position={{ bottom: 24, right: 24 }}>
      <Transition transition="slide-up" mounted={scroll.y > 300}>
        {(transitionStyles) => (
          <ActionIcon
            style={transitionStyles}
            size="xl"
            radius="xl"
            variant="filled"
            aria-label="맨 위로"
            onClick={() => scrollTo({ y: 0 })}
          >
            <IconArrowUp size={22} />
          </ActionIcon>
        )}
      </Transition>
    </Affix>
  );
}
