import { useEffect, useRef } from "react";

// Supported coin logos array for rotation
const logos = ["bitcoin-logo.png", "ethereum-logo.png", "usdt-logo.png", "project-logo.png"];

export default function CoinFlip() {
  const coinRef = useRef<HTMLDivElement>(null);
  const frontRef = useRef<HTMLImageElement>(null);
  const backRef = useRef<HTMLImageElement>(null);
  const indexRef = useRef(0);

  useEffect(() => {
    const coin = coinRef.current;
    const front = frontRef.current;
    const back = backRef.current;
    if (!coin || !front || !back) return;

    let timer: ReturnType<typeof setTimeout>;
    let rotation = 0;

    const flip = () => {
      const nextIndex = (indexRef.current + 1) % logos.length;
      const incoming = rotation % 360 === 0 ? back : front;
      indexRef.current = nextIndex;
      rotation += 180;
      coin.style.transform = `rotateY(${rotation}deg)`;
      timer = setTimeout(() => {
        if (incoming) {
          incoming.src = `/${logos[nextIndex]}`;
        }
      }, 1200);
      timer = setTimeout(flip, 4200);
    };

    timer = setTimeout(flip, 1500);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="coin-flip-wrap" aria-hidden="true">
      <div className="coin-glow" />
      <div className="coin-stage">
        <div ref={coinRef} className="coin-flip">
          <div className="coin-face coin-front">
            <img ref={frontRef} src={`/${logos[0]}`} alt="" />
          </div>
          <div className="coin-face coin-back">
            <img ref={backRef} src={`/${logos[1]}`} alt="" />
          </div>
        </div>
      </div>
    </div>
  );
}

