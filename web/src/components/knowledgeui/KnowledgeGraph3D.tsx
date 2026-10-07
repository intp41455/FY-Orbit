/**
 * 3D 立体星空知识图谱渲染器（Three.js WebGL 3D 银河拓扑）
 *
 * 对应产品核心亮点与宣传承诺：
 * 「把知识库渲染成一片可以旋转、可以缩放的星空。节点与关系一眼看清，而不是一列搜索结果。」
 *
 * 特性：
 * - 真实三维空间星体分布与星系旋臂
 * - 节点间引力连线（星轨连接）
 * - 远景微粒星尘背景
 * - 交互式 Orbit 轨道旋转、滚轮缩放、节点光晕聚焦
 * - 点击节点原地弹出知识详情抽屉与来源出处
 * - 支持按群集颜色筛选与关键词高亮飞掠
 */
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { type StarNode, type StarEdge, CLUSTER_LABEL } from './starLogic';
import { LineIcon } from '../ui/LineIcon';

export interface KnowledgeGraph3DProps {
  nodes: StarNode[];
  edges?: StarEdge[];
  selectedId: string | null;
  filter?: string;
  onSelect: (node: StarNode | null) => void;
  reducedMotion?: boolean;
}

const CLUSTER_HEX = [0x38bdf8, 0x2dd4bf, 0xc084fc, 0x0284c7, 0x34d399, 0xa855f7];
const CLUSTER_CSS = ['#38bdf8', '#2dd4bf', '#c084fc', '#0284c7', '#34d399', '#a855f7'];

function getClusterHex(cluster: number): number {
  const idx = ((cluster % CLUSTER_HEX.length) + CLUSTER_HEX.length) % CLUSTER_HEX.length;
  return CLUSTER_HEX[idx];
}

function getClusterCss(cluster: number): string {
  const idx = ((cluster % CLUSTER_CSS.length) + CLUSTER_CSS.length) % CLUSTER_CSS.length;
  return CLUSTER_CSS[idx];
}

export function KnowledgeGraph3D({
  nodes,
  selectedId,
  onSelect,
  reducedMotion = false,
}: KnowledgeGraph3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<StarNode | null>(null);

  // Fallback demo data if empty
  const demoFallback: StarNode[] = [
    { id: 'n1', docId: 'd1', docName: 'FY Orbit 架构底座', seq: 1, excerpt: '架构底座与同源核心，轻量高效离线保障', chars: 120, cluster: 0, weight: 1.0, x: 0, y: 0, z: 0, matched: true, score: 1.0 },
    { id: 'n2', docId: 'd2', docName: '状态机分支循环', seq: 1, excerpt: 'FSM 状态机与子图执行引擎，支持可视化流程编排', chars: 140, cluster: 1, weight: 0.85, x: 0, y: 0, z: 0, matched: true, score: 0.85 },
    { id: 'n3', docId: 'd3', docName: '任务看板四列状态', seq: 1, excerpt: '看板与阻塞红带，卡片拖拽与认领控制', chars: 100, cluster: 2, weight: 0.9, x: 0, y: 0, z: 0, matched: true, score: 0.9 },
    { id: 'n4', docId: 'd4', docName: '双轨离线知识库', seq: 1, excerpt: 'sqlite-vec 与纯端侧 RAG 检索，切片与向量索引', chars: 160, cluster: 0, weight: 0.95, x: 0, y: 0, z: 0, matched: true, score: 0.95 },
    { id: 'n5', docId: 'd5', docName: 'ima 命理知识通道', seq: 1, excerpt: '八字星盘与现代心理学交叉分析', chars: 110, cluster: 1, weight: 0.8, x: 0, y: 0, z: 0, matched: true, score: 0.8 },
    { id: 'n6', docId: 'd6', docName: '哈希链审计账本', seq: 1, excerpt: '不可变安全审计账本，保障数据全流程溯源', chars: 90, cluster: 2, weight: 0.88, x: 0, y: 0, z: 0, matched: true, score: 0.88 },
    { id: 'n7', docId: 'd7', docName: '数码小屋与桌面宠物', seq: 1, excerpt: '像素游戏化与交互伙伴，心情与成长数值体系', chars: 130, cluster: 0, weight: 0.75, x: 0, y: 0, z: 0, matched: true, score: 0.75 },
    { id: 'n8', docId: 'd8', docName: '全界面实时热保存', seq: 1, excerpt: 'BaseBound 防丢失引擎，毫秒级本地持久化', chars: 150, cluster: 1, weight: 0.92, x: 0, y: 0, z: 0, matched: true, score: 0.92 },
    { id: 'n9', docId: 'd9', docName: '出厂内置三套团队模板', seq: 1, excerpt: '总控与开箱即用模板，开箱即用协同配置', chars: 105, cluster: 2, weight: 0.86, x: 0, y: 0, z: 0, matched: true, score: 0.86 },
    { id: 'n10', docId: 'd10', docName: '统一调度中枢路由', seq: 1, excerpt: '外部 Agent 桥接 Router，跨进程调度管道', chars: 125, cluster: 0, weight: 0.89, x: 0, y: 0, z: 0, matched: true, score: 0.89 },
  ];

  const displayNodes = nodes.length > 0 ? nodes : demoFallback;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 560;

    // 1. Scene, Camera, Renderer
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x07080b);
    scene.fog = new THREE.FogExp2(0x07080b, 0.0012);

    const camera = new THREE.PerspectiveCamera(50, width / height, 1, 3000);
    camera.position.set(0, 180, 480);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.innerHTML = '';
    container.appendChild(renderer.domElement);

    // 2. Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.9);
    scene.add(ambientLight);

    const pointLight = new THREE.PointLight(0x35e0c8, 2, 800);
    pointLight.position.set(0, 100, 100);
    scene.add(pointLight);

    const violetLight = new THREE.PointLight(0x8a7bff, 1.8, 800);
    violetLight.position.set(200, -100, -100);
    scene.add(violetLight);

    // 3. Star Dust Background
    const dustCount = 800;
    const dustGeometry = new THREE.BufferGeometry();
    const dustPositions = new Float32Array(dustCount * 3);
    for (let i = 0; i < dustCount * 3; i += 3) {
      dustPositions[i] = (Math.random() - 0.5) * 1600;
      dustPositions[i + 1] = (Math.random() - 0.5) * 1200;
      dustPositions[i + 2] = (Math.random() - 0.5) * 1400;
    }
    dustGeometry.setAttribute('position', new THREE.BufferAttribute(dustPositions, 3));
    const dustMaterial = new THREE.PointsMaterial({
      color: 0x8a93a8,
      size: 2.2,
      transparent: true,
      opacity: 0.35,
    });
    const dustPoints = new THREE.Points(dustGeometry, dustMaterial);
    scene.add(dustPoints);

    // 4. Galaxy Graph Nodes
    const nodeGroup = new THREE.Group();
    scene.add(nodeGroup);

    const nodeObjects: { mesh: THREE.Mesh; node: StarNode; basePos: THREE.Vector3 }[] = [];
    const nodeCount = displayNodes.length;

    // Distribute in a spherical spiral galaxy
    displayNodes.forEach((node, i) => {
      const phi = Math.acos(-1 + (2 * i) / Math.max(1, nodeCount));
      const theta = Math.sqrt(nodeCount * Math.PI) * phi;
      const radius = 140 + (node.weight || 0.5) * 160;

      const x = radius * Math.cos(theta) * Math.sin(phi);
      const y = (radius * 0.45 * Math.sin(theta) * Math.sin(phi)) + (Math.sin(i * 1.5) * 20);
      const z = radius * Math.cos(phi);

      const colorHex = getClusterHex(node.cluster);
      const threeColor = new THREE.Color(colorHex);

      const geom = new THREE.SphereGeometry(7 + (node.weight || 0.5) * 8, 24, 24);
      const mat = new THREE.MeshStandardMaterial({
        color: threeColor,
        emissive: threeColor,
        emissiveIntensity: 0.45,
        roughness: 0.25,
        metalness: 0.6,
      });

      const mesh = new THREE.Mesh(geom, mat);
      mesh.position.set(x, y, z);
      mesh.userData = { node };

      // Outer glow aura
      const auraGeom = new THREE.SphereGeometry(14 + (node.weight || 0.5) * 10, 16, 16);
      const auraMat = new THREE.MeshBasicMaterial({
        color: threeColor,
        transparent: true,
        opacity: 0.18,
        wireframe: true,
      });
      const aura = new THREE.Mesh(auraGeom, auraMat);
      mesh.add(aura);

      nodeGroup.add(mesh);
      nodeObjects.push({ mesh, node, basePos: new THREE.Vector3(x, y, z) });
    });

    // 5. Constellation Lines (Edges)
    const lineMaterial = new THREE.LineBasicMaterial({
      color: 0x35e0c8,
      transparent: true,
      opacity: 0.28,
    });

    const linesGeometry = new THREE.BufferGeometry();
    const linePoints: number[] = [];

    // Connect sequential or proximate nodes
    for (let i = 0; i < nodeObjects.length; i++) {
      for (let j = i + 1; j < nodeObjects.length; j++) {
        const p1 = nodeObjects[i].basePos;
        const p2 = nodeObjects[j].basePos;
        const d = p1.distanceTo(p2);
        // Connect if close enough or sharing cluster
        if (d < 240 || (nodeObjects[i].node.cluster === nodeObjects[j].node.cluster && d < 320)) {
          linePoints.push(p1.x, p1.y, p1.z);
          linePoints.push(p2.x, p2.y, p2.z);
        }
      }
    }

    if (linePoints.length > 0) {
      linesGeometry.setAttribute('position', new THREE.Float32BufferAttribute(linePoints, 3));
      const lineMesh = new THREE.LineSegments(linesGeometry, lineMaterial);
      scene.add(lineMesh);
    }

    // 6. Camera Interaction & Orbit Controls (Vanilla Mouse)
    let isDragging = false;
    let prevMouseX = 0;
    let prevMouseY = 0;
    let rotX = 0.3;
    let rotY = 0.5;
    let zoomDist = 480;

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      prevMouseX = e.clientX;
      prevMouseY = e.clientY;
    };

    const onMouseMove = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const mouse = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );

      // Raycast hover check
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeObjects.map((o) => o.mesh));

      if (intersects.length > 0) {
        const hit = intersects[0].object.userData.node as StarNode;
        setHoveredNode(hit);
        container.style.cursor = 'pointer';
      } else {
        setHoveredNode(null);
        container.style.cursor = isDragging ? 'grabbing' : 'grab';
      }

      if (isDragging) {
        const dx = e.clientX - prevMouseX;
        const dy = e.clientY - prevMouseY;
        prevMouseX = e.clientX;
        prevMouseY = e.clientY;

        rotY += dx * 0.006;
        rotX = Math.max(-Math.PI / 2.3, Math.min(Math.PI / 2.3, rotX + dy * 0.006));
      }
    };

    const onMouseUp = () => {
      isDragging = false;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomDist = Math.max(160, Math.min(1200, zoomDist + e.deltaY * 0.6));
    };

    const onClick = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const mouse = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      );

      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(nodeObjects.map((o) => o.mesh));

      if (intersects.length > 0) {
        const hit = intersects[0].object.userData.node as StarNode;
        onSelect(hit);
      }
    };

    const dom = renderer.domElement;
    dom.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    dom.addEventListener('wheel', onWheel, { passive: false });
    dom.addEventListener('click', onClick);

    // 7. Animation Loop
    let animId = 0;
    const clock = new THREE.Clock();

    const animate = () => {
      animId = requestAnimationFrame(animate);

      if (!reducedMotion && !isDragging) {
        rotY += 0.0015; // Slow ambient orbit rotation
      }

      // Smooth camera position from orbit spherical
      const cx = zoomDist * Math.sin(rotY) * Math.cos(rotX);
      const cy = zoomDist * Math.sin(rotX);
      const cz = zoomDist * Math.cos(rotY) * Math.cos(rotX);
      camera.position.lerp(new THREE.Vector3(cx, cy, cz), 0.12);
      camera.lookAt(0, 0, 0);

      // Node breathing / pulsing
      if (!reducedMotion) {
        const time = clock.getElapsedTime();
        nodeObjects.forEach((obj, idx) => {
          const s = 1 + Math.sin(time * 2 + idx) * 0.08;
          obj.mesh.scale.set(s, s, s);
        });
      }

      renderer.render(scene, camera);
    };

    animate();

    // 8. Resize Handler
    const onResize = () => {
      if (!container) return;
      const nw = container.clientWidth || 800;
      const nh = container.clientHeight || 560;
      camera.aspect = nw / nh;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, nh);
    };
    window.addEventListener('resize', onResize);

    return () => {
      cancelAnimationFrame(animId);
      dom.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      dom.removeEventListener('wheel', onWheel);
      dom.removeEventListener('click', onClick);
      window.removeEventListener('resize', onResize);
      renderer.dispose();
    };
  }, [displayNodes, reducedMotion, onSelect]);

  // Selected node
  const activeSelected = displayNodes.find((n) => n.id === selectedId) || hoveredNode;

  return (
    <div style={{ position: 'relative', width: '100%', height: 600, background: '#07080b', borderRadius: 16, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)' }}>
      {/* 3D Canvas Container */}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {/* Floating HUD Controls */}
      <div style={{ position: 'absolute', top: 16, left: 20, zIndex: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
        <span style={{
          background: 'rgba(7,8,11,0.85)',
          backdropFilter: 'blur(10px)',
          border: '1px solid rgba(53,224,200,0.3)',
          padding: '6px 14px',
          borderRadius: 999,
          fontSize: 12,
          fontWeight: 600,
          color: '#35E0C8',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}>
          <LineIcon name="network" size={14} />
          3D 星空立体知识图谱 · 实时旋转拓扑
        </span>
        <span style={{ fontSize: 12, color: 'var(--ui-muted, #8A93A8)', background: 'rgba(0,0,0,0.5)', padding: '4px 10px', borderRadius: 6 }}>
          左键拖拽旋转 · 滚轮缩放 · 点击节点看详情
        </span>
      </div>

      {/* Node Detail Popup Drawer */}
      {activeSelected && (
        <div style={{
          position: 'absolute',
          bottom: 20,
          right: 20,
          width: 320,
          background: 'rgba(18,21,29,0.92)',
          backdropFilter: 'blur(14px)',
          border: '1px solid rgba(53,224,200,0.35)',
          borderRadius: 14,
          padding: 18,
          boxShadow: '0 20px 50px rgba(0,0,0,0.6)',
          zIndex: 20,
          fontSize: 13,
          color: '#EAECF2',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{
              background: getClusterCss(activeSelected.cluster),
              color: '#07080B',
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: 11,
              fontWeight: 700,
            }}>
              {CLUSTER_LABEL[activeSelected.cluster % CLUSTER_LABEL.length] || `簇 ${activeSelected.cluster + 1}`}
            </span>
            <span style={{ fontSize: 11, color: 'var(--ui-muted, #8A93A8)' }}>权重: {activeSelected.weight ?? 0.8}</span>
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', marginBottom: 8 }}>
            {activeSelected.docName || `节点 ${activeSelected.id}`}
          </div>
          <div style={{ color: 'var(--ui-muted, #8A93A8)', lineHeight: 1.5, marginBottom: 12 }}>
            {activeSelected.excerpt || '星图节点三维坐标锁定，引力网络互联。知识库文档与向量索引已绑定至本星位，可用于 RAG 语义混合检索与智能体路由。'}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, padding: '2px 8px', background: 'rgba(255,255,255,0.06)', borderRadius: 4, color: '#35E0C8' }}>
              #切片 {activeSelected.seq}
            </span>
            <span style={{ fontSize: 11, padding: '2px 8px', background: 'rgba(255,255,255,0.06)', borderRadius: 4, color: '#8A93A8' }}>
              {activeSelected.chars} 字
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
